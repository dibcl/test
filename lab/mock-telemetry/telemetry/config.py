from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent import AgentSettings
from .clocks import BaseClock, RealClock, SimulatedClock
from .providers import (
    BaseMetricsProvider,
    FrozenProfileProvider,
    HybridSyntheticNetworkProvider,
    LiveSystemProvider,
    SyntheticMetricsProvider,
)
from .registry import CLOCK_REGISTRY, PROVIDER_REGISTRY, TRANSPORT_REGISTRY, Registry
from .transports import BaseTransport, FileDumpTransport, MemoryTransport, TcpTransport, UdpTransport
from .windows_validation import WindowsValidationProvider


def _strict_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _strict_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _strict_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _strict_type_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _build_live_system(cfg: Mapping[str, Any]) -> LiveSystemProvider:
    """Build the invasive diagnostic collector only with an explicit opt-in.

    The intended runtime is hybrid_network. live_system inspects host CPU,
    memory, disk, process and hostname state and must never be selected by an
    accidental config typo or legacy alias.
    """
    if cfg.get("diagnostic_only") is not True:
        raise ValueError(
            "live_system is diagnostic-only; set provider.diagnostic_only=true explicitly"
        )
    return LiveSystemProvider(
        process_limit=_strict_int(cfg.get("process_limit", 20), "provider.process_limit"),
        disk_path=cfg.get("disk_path"),
    )


def _register_defaults() -> None:
    PROVIDER_REGISTRY.register(
        "frozen_profile",
        lambda cfg: FrozenProfileProvider(cfg["profile"]),
        aliases=("frozen",),
        replace=True,
    )
    PROVIDER_REGISTRY.register(
        "synthetic",
        lambda cfg: SyntheticMetricsProvider(cfg["profile"]),
        replace=True,
    )
    PROVIDER_REGISTRY.register(
        "hybrid_network",
        lambda cfg: HybridSyntheticNetworkProvider(cfg["profile"]),
        aliases=("hybrid", "synthetic_live_network"),
        replace=True,
    )
    PROVIDER_REGISTRY.register(
        "windows_validation",
        lambda cfg: WindowsValidationProvider(cfg["profile"], cfg["local_env"]),
        aliases=("windows_current", "current_windows"),
        replace=True,
    )
    PROVIDER_REGISTRY.register(
        "live_system",
        _build_live_system,
        aliases=("live",),
        replace=True,
    )

    TRANSPORT_REGISTRY.register("memory", lambda cfg: MemoryTransport(), replace=True)
    TRANSPORT_REGISTRY.register(
        "file_dump",
        lambda cfg: FileDumpTransport(cfg["path"]),
        aliases=("file",),
        replace=True,
    )
    TRANSPORT_REGISTRY.register(
        "tcp",
        lambda cfg: TcpTransport(
            host=str(cfg.get("host", "127.0.0.1")),
            port=_strict_int(cfg["port"], "transport.port"),
            timeout=_strict_float(cfg.get("timeout", 5.0), "transport.timeout"),
            allow_public=_strict_bool(cfg.get("allow_public", False), "transport.allow_public"),
        ),
        aliases=("loopback_tcp",),
        replace=True,
    )
    TRANSPORT_REGISTRY.register(
        "udp",
        lambda cfg: UdpTransport(
            host=str(cfg.get("host", "127.0.0.1")),
            port=_strict_int(cfg["port"], "transport.port"),
            allow_public=_strict_bool(cfg.get("allow_public", False), "transport.allow_public"),
        ),
        replace=True,
    )

    CLOCK_REGISTRY.register("real", lambda cfg: RealClock(), replace=True)

    def build_simulated(cfg: Mapping[str, Any]) -> SimulatedClock:
        start = cfg.get("start")
        if start is None:
            return SimulatedClock()
        if not isinstance(start, str) or not start.strip():
            raise ValueError("clock.start must be a non-empty ISO-8601 string")
        return SimulatedClock(datetime.fromisoformat(start))

    CLOCK_REGISTRY.register(
        "simulated",
        build_simulated,
        aliases=("fake",),
        replace=True,
    )


_register_defaults()


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fp:
        config = json.load(fp)
    if not isinstance(config, dict):
        raise ValueError("top-level config must be an object")
    return config


def register_provider(
    name: str,
    factory,
    *,
    aliases: tuple[str, ...] = (),
    replace: bool = False,
) -> None:
    PROVIDER_REGISTRY.register(name, factory, aliases=aliases, replace=replace)


def register_transport(
    name: str,
    factory,
    *,
    aliases: tuple[str, ...] = (),
    replace: bool = False,
) -> None:
    TRANSPORT_REGISTRY.register(name, factory, aliases=aliases, replace=replace)


def register_clock(
    name: str,
    factory,
    *,
    aliases: tuple[str, ...] = (),
    replace: bool = False,
) -> None:
    CLOCK_REGISTRY.register(name, factory, aliases=aliases, replace=replace)


def build_provider(
    cfg: Mapping[str, Any],
    registry: Registry[BaseMetricsProvider] = PROVIDER_REGISTRY,
) -> BaseMetricsProvider:
    provider_cfg = cfg.get("provider")
    if provider_cfg is None:
        if "profile" not in cfg:
            raise ValueError("provider or profile required")
        provider_cfg = {"type": "frozen_profile", "profile": cfg["profile"]}
    if not isinstance(provider_cfg, Mapping):
        raise ValueError("provider must be an object")
    provider_type = _strict_type_name(provider_cfg.get("type", "frozen_profile"), "provider.type")
    return registry.build(provider_type, provider_cfg)


def build_transport(
    cfg: Mapping[str, Any],
    registry: Registry[BaseTransport] = TRANSPORT_REGISTRY,
) -> BaseTransport:
    transport_cfg = cfg.get("transport", {"type": "memory"})
    if not isinstance(transport_cfg, Mapping):
        raise ValueError("transport must be an object")
    kind = _strict_type_name(transport_cfg.get("type", "memory"), "transport.type")
    return registry.build(kind, transport_cfg)


def build_clock(
    cfg: Mapping[str, Any],
    registry: Registry[BaseClock] = CLOCK_REGISTRY,
) -> BaseClock:
    clock_cfg = cfg.get("clock", {"type": "real"})
    if not isinstance(clock_cfg, Mapping):
        raise ValueError("clock must be an object")
    kind = _strict_type_name(clock_cfg.get("type", "real"), "clock.type")
    return registry.build(kind, clock_cfg)


def build_settings(cfg: Mapping[str, Any]) -> AgentSettings:
    duration = cfg.get("duration_seconds")
    settings = AgentSettings(
        interval_seconds=_strict_float(cfg.get("interval_seconds", 1.0), "interval_seconds"),
        duration_seconds=_strict_float(duration, "duration_seconds") if duration is not None else None,
        schema_version=_strict_int(cfg.get("schema_version", 1), "schema_version"),
        provider_health_timeout=_strict_float(
            cfg.get("provider_health_timeout", 5.0), "provider_health_timeout"
        ),
    )
    settings.validate()
    return settings
