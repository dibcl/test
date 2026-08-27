from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from message_adapters import build_message_adapter

from .agent import TelemetryAgent
from .config import build_clock, build_provider, build_settings, build_transport, load_config
from .model import ProviderSwitchResult


class RuntimeState(str, Enum):
    CREATED = "created"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    SWITCHING = "switching"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(slots=True)
class RuntimeStatus:
    state: RuntimeState
    active_provider: str
    generation: int = 0
    successful_reloads: int = 0
    failed_reloads: int = 0
    restart_required: bool = False
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


class ConfigFileWatcher:
    """Poll a local JSON config and apply provider-only live changes.

    The watcher uses ordinary wall-clock waits even when the telemetry agent is
    running on a simulated clock. A malformed partial write is retried when its
    content changes, while an unchanged malformed file is reported only once.
    A syntactically valid but rejected configuration is also recorded once for
    that file content and leaves the current provider running.
    """

    def __init__(
        self,
        runtime: "TelemetryRuntime",
        path: str | Path,
        poll_seconds: float = 1.0,
    ) -> None:
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("reload poll_seconds must be finite and > 0")
        self.runtime = runtime
        self.path = Path(path)
        self.poll_seconds = poll_seconds
        self._stop_event = asyncio.Event()
        self._last_digest: str | None = None
        self._last_failed_digest: str | None = None
        self._last_read_error: str | None = None

    @staticmethod
    def _digest(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    async def stop(self) -> None:
        self._stop_event.set()

    async def _wait_for_poll(self) -> bool:
        """Return False when stop was requested, True when the poll interval elapsed."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_seconds)
            return False
        except asyncio.TimeoutError:
            return True

    async def run(self) -> None:
        try:
            if self.path.exists():
                self._last_digest = self._digest(await asyncio.to_thread(self.path.read_bytes))
        except OSError as exc:
            message = f"config read failed: {exc}"
            self.runtime.record_reload_failure(message)
            self._last_read_error = message

        while not self._stop_event.is_set():
            if not await self._wait_for_poll():
                break

            try:
                raw = await asyncio.to_thread(self.path.read_bytes)
            except OSError as exc:
                message = f"config read failed: {exc}"
                if message != self._last_read_error:
                    self.runtime.record_reload_failure(message)
                    self._last_read_error = message
                continue

            had_read_error = self._last_read_error is not None
            self._last_read_error = None
            digest = self._digest(raw)
            if digest == self._last_digest:
                if self._last_failed_digest is not None or had_read_error:
                    self.runtime.clear_reload_failure()
                self._last_failed_digest = None
                continue

            try:
                decoded = json.loads(raw.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ValueError("top-level config must be an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                if digest != self._last_failed_digest:
                    self.runtime.record_reload_failure(f"config parse failed: {exc}")
                    self._last_failed_digest = digest
                continue

            self._last_failed_digest = None
            try:
                await self.runtime.apply_config(decoded)
            except Exception as exc:
                self.runtime.record_reload_failure(f"config apply failed: {exc}")
            finally:
                self._last_digest = digest


class TelemetryRuntime:
    """Managed runtime facade with transactional provider hot switching."""

    _RESTART_KEYS = {
        "transport",
        "clock",
        "interval_seconds",
        "duration_seconds",
        "schema_version",
        "provider_health_timeout",
        "message_adapter",
    }

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        config_path: str | Path | None = None,
    ) -> None:
        self.config = dict(config)
        self.desired_config = dict(config)
        self.config_path = Path(config_path) if config_path is not None else None
        self.agent = TelemetryAgent(
            provider=build_provider(self.config),
            transport=build_transport(self.config),
            clock=build_clock(self.config),
            settings=build_settings(self.config),
            message_adapter=build_message_adapter(self.config),
        )
        self._state = RuntimeState.READY
        self._generation = 0
        self._successful_reloads = 0
        self._failed_reloads = 0
        self._restart_required = False
        self._last_error: str | None = None
        self._reload_failure_active = False
        self._watcher: ConfigFileWatcher | None = None
        self._watcher_task: asyncio.Task[None] | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> "TelemetryRuntime":
        path_obj = Path(path)
        return cls(load_config(path_obj), config_path=path_obj)

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            state=self._state,
            active_provider=self.agent.provider.name,
            generation=self._generation,
            successful_reloads=self._successful_reloads,
            failed_reloads=self._failed_reloads,
            restart_required=self._restart_required,
            last_error=self._last_error,
        )

    @staticmethod
    def _normalized_provider_config(config: Mapping[str, Any]) -> dict[str, Any]:
        provider = config.get("provider")
        if provider is not None:
            if not isinstance(provider, Mapping):
                raise ValueError("provider must be an object")
            return dict(provider)
        if "profile" in config:
            return {"type": "frozen_profile", "profile": config["profile"]}
        raise ValueError("provider or profile required")

    def _requires_restart(self, new_config: Mapping[str, Any]) -> bool:
        return any(self.config.get(key) != new_config.get(key) for key in self._RESTART_KEYS)

    def record_reload_failure(self, message: str) -> None:
        self._failed_reloads += 1
        self._reload_failure_active = True
        self._last_error = message
        if self._state not in {RuntimeState.STOPPING, RuntimeState.STOPPED, RuntimeState.FAILED}:
            self._state = RuntimeState.DEGRADED

    def clear_reload_failure(self) -> None:
        """Clear only an active watcher/reload failure, preserving other errors."""
        if not self._reload_failure_active:
            return
        self._reload_failure_active = False
        self._last_error = None
        if self._state == RuntimeState.DEGRADED:
            self._state = RuntimeState.RUNNING if self.agent.running else RuntimeState.READY

    async def switch_provider(
        self,
        provider_config: Mapping[str, Any],
    ) -> ProviderSwitchResult:
        """Build, probe, and transactionally activate a new provider."""
        previous_state = self._state
        self._state = RuntimeState.SWITCHING
        try:
            new_provider = build_provider({"provider": dict(provider_config)})
            result = await self.agent.set_provider(new_provider)
        except Exception as exc:
            self._last_error = f"provider switch failed: {exc}"
            self._state = RuntimeState.DEGRADED
            raise

        self.config["provider"] = dict(provider_config)
        self.config.pop("profile", None)
        self._generation += int(result.changed)
        self._last_error = result.cleanup_error
        if result.cleanup_error:
            self._state = RuntimeState.DEGRADED
        elif self.agent.running:
            self._state = RuntimeState.RUNNING
        elif previous_state in {RuntimeState.STOPPED, RuntimeState.STOPPING}:
            self._state = previous_state
        else:
            self._state = RuntimeState.READY
        return result

    async def apply_config(self, new_config: Mapping[str, Any]) -> None:
        """Apply the live-safe subset of a newly loaded configuration.

        Provider changes are transactional. Transport, clock, scheduling, and
        schema changes are retained as desired configuration and reported through
        restart_required rather than mutating a running agent in place.
        """
        if not isinstance(new_config, Mapping):
            raise ValueError("top-level config must be an object")

        new_provider_cfg = self._normalized_provider_config(new_config)
        old_provider_cfg = self._normalized_provider_config(self.config)
        self.desired_config = dict(new_config)
        self._restart_required = self._requires_restart(new_config)

        switch_result: ProviderSwitchResult | None = None
        if new_provider_cfg != old_provider_cfg:
            switch_result = await self.switch_provider(new_provider_cfg)

        if "reload" in new_config:
            self.config["reload"] = new_config["reload"]

        self._successful_reloads += 1
        self._reload_failure_active = False

        # A valid configuration supersedes an earlier reload/parse error. Keep a
        # cleanup warning from the current switch, but otherwise return from
        # DEGRADED to the normal active state.
        if switch_result is None or switch_result.cleanup_error is None:
            self._last_error = None
            if self._state == RuntimeState.DEGRADED:
                self._state = RuntimeState.RUNNING if self.agent.running else RuntimeState.READY

    def _reload_settings(self) -> tuple[bool, float]:
        raw = self.config.get("reload", {})
        if raw is None:
            return False, 1.0
        if not isinstance(raw, Mapping):
            raise ValueError("reload must be an object")
        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("reload.enabled must be a boolean")
        poll_value = raw.get("poll_seconds", 1.0)
        if isinstance(poll_value, bool) or not isinstance(poll_value, (int, float)):
            raise ValueError("reload.poll_seconds must be a number")
        poll_seconds = float(poll_value)
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("reload.poll_seconds must be finite and > 0")
        return enabled, poll_seconds

    async def _start_watcher(self) -> None:
        enabled, poll_seconds = self._reload_settings()
        if not enabled:
            return
        if self.config_path is None:
            raise ValueError("reload.enabled requires TelemetryRuntime.from_file()")
        self._watcher = ConfigFileWatcher(self, self.config_path, poll_seconds)
        self._watcher_task = asyncio.create_task(self._watcher.run(), name="telemetry-config-watcher")

    async def _stop_watcher(self) -> None:
        if self._watcher is not None:
            await self._watcher.stop()
        if self._watcher_task is not None:
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
        self._watcher = None
        self._watcher_task = None

    async def run(self) -> None:
        if self._state in {RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.SWITCHING}:
            raise RuntimeError(f"runtime cannot start from state {self._state.value}")

        self._state = RuntimeState.STARTING
        try:
            await self._start_watcher()
            self._state = RuntimeState.RUNNING
            await self.agent.run()
            self._state = RuntimeState.STOPPED
        except Exception as exc:
            self._last_error = str(exc)
            self._state = RuntimeState.FAILED
            raise
        finally:
            await self._stop_watcher()

    async def stop(self) -> None:
        if self._state in {RuntimeState.STOPPED, RuntimeState.FAILED}:
            return
        self._state = RuntimeState.STOPPING
        if self._watcher is not None:
            await self._watcher.stop()
        await self.agent.stop()
