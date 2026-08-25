from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

from .clocks import BaseClock
from .model import ProviderHealth, ProviderSwitchResult, TelemetrySnapshot
from .providers import BaseMetricsProvider
from .transports import BaseTransport


@dataclass(slots=True)
class AgentSettings:
    interval_seconds: float = 1.0
    duration_seconds: float | None = None
    schema_version: int = 1
    provider_health_timeout: float = 5.0

    def validate(self) -> None:
        if not math.isfinite(self.interval_seconds) or self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be finite and > 0")
        if self.duration_seconds is not None and (
            not math.isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("duration_seconds must be finite and >= 0")
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("schema_version must be an integer >= 1")
        if not math.isfinite(self.provider_health_timeout) or self.provider_health_timeout <= 0:
            raise ValueError("provider_health_timeout must be finite and > 0")


class TelemetryAgent:
    def __init__(
        self,
        provider: BaseMetricsProvider,
        transport: BaseTransport,
        clock: BaseClock,
        settings: AgentSettings,
    ) -> None:
        settings.validate()
        self._provider = provider
        self._transport = transport
        self._clock = clock
        self._settings = settings
        self._provider_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._running = False
        self._provider_started = False
        self._transport_open = False

    @property
    def provider(self) -> BaseMetricsProvider:
        return self._provider

    @property
    def running(self) -> bool:
        return self._running

    async def _health(self, provider: BaseMetricsProvider) -> ProviderHealth:
        return await asyncio.wait_for(
            provider.health_check(),
            timeout=self._settings.provider_health_timeout,
        )

    async def set_provider(self, provider: BaseMetricsProvider) -> ProviderSwitchResult:
        """Transactionally replace the active provider.

        A candidate is started and health-checked before it becomes active. If
        activation fails, the current provider is left untouched. When the agent
        is not running the candidate is probed and stopped again, then retained
        as the provider that will be started by the next run().
        """
        async with self._provider_lock:
            old = self._provider
            if provider is old:
                health = await self._health(old)
                return ProviderSwitchResult(old.name, old.name, False, health)

            candidate_started = False
            try:
                await provider.start()
                candidate_started = True
                health = await self._health(provider)
                if not health.healthy:
                    raise RuntimeError(health.detail or "provider health check failed")
            except Exception:
                if candidate_started:
                    try:
                        await provider.stop()
                    except Exception:
                        pass
                raise

            cleanup_error: str | None = None
            if self._running:
                self._provider = provider
                self._provider_started = True
                try:
                    if old is not provider:
                        await old.stop()
                except Exception as exc:
                    cleanup_error = str(exc)
            else:
                # The probe succeeded. Keep the candidate configured but do not
                # leave provider resources open before run() starts.
                try:
                    await provider.stop()
                finally:
                    self._provider = provider
                    self._provider_started = False

            return ProviderSwitchResult(
                previous_provider=old.name,
                active_provider=provider.name,
                changed=True,
                health=health,
                cleanup_error=cleanup_error,
            )

    async def stop(self) -> None:
        self._stop_event.set()

    async def _collect(self) -> TelemetrySnapshot:
        async with self._provider_lock:
            return await self._provider.snapshot(self._clock)

    async def run(self) -> None:
        self._settings.validate()
        if self._running:
            raise RuntimeError("agent is already running")

        self._stop_event.clear()
        elapsed = 0.0

        try:
            async with self._provider_lock:
                await self._provider.start()
                self._provider_started = True
                health = await self._health(self._provider)
                if not health.healthy:
                    raise RuntimeError(health.detail or "initial provider is unhealthy")

            await self._transport.open()
            self._transport_open = True
            self._running = True

            while not self._stop_event.is_set():
                snapshot = await self._collect()
                await self._transport.send(
                    snapshot.to_envelope(schema_version=self._settings.schema_version)
                )

                if (
                    self._settings.duration_seconds is not None
                    and elapsed >= self._settings.duration_seconds
                ):
                    break

                interval = self._settings.interval_seconds
                await self._clock.sleep(interval)
                elapsed += interval
        finally:
            self._running = False
            async with self._provider_lock:
                if self._provider_started:
                    try:
                        await self._provider.stop()
                    finally:
                        self._provider_started = False
            if self._transport_open:
                try:
                    await self._transport.close()
                finally:
                    self._transport_open = False
