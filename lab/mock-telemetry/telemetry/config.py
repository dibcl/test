from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent import AgentSettings
from .clocks import BaseClock, RealClock, SimulatedClock
from .providers import (
    BaseMetricsProvider,
    FrozenProfileProvider,
    LiveSystemProvider,
    SyntheticMetricsProvider,
)
from .registry import CLOCK_REGISTRY, PROVIDER_REGISTRY, TRANSPORT_REGISTRY, Registry
from .transports import BaseTransport, FileDumpTransport, MemoryTransport, TcpTransport, UdpTransport


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
        "live_system",
        lambda cfg: LiveSystemProvider(
            process_limit=int(cfg.get("process_limit", 20)),
            disk_path=cfg.get("disk_path"),
        ),
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
            port=int(cfg["port"]),
            timeout=float(cfg.get("timeout", 5.0)),
            allow_public=bool(cfg.get("allow_public", False)),
        ),
        aliases=("loopback_tcp",),
        replace=True,
    )
    TRANSPORT_REGISTRY.register(
        "udp",
        lambda cfg: UdpTransport(
            host=str(cfg.get("host", "127.0.0.1")),
            port=int(cfg["port"]),
            allow_public=bool(cfg.get("allow_public", False)),
        ),
        replace=True,
    )

    CLOCK_REGISTRY.register("real", lambda cfg: RealClock(), replace=True)
    CLOCK_REGISTRY.register(
        "simulated",
        lambda cfg: SimulatedClock(
            datetime.fromisoformat(str(cfg["start"])) if cfg.get("start") else None
        ),
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

    # Backward compatibility with the original profile-only configuration.
    if provider_cfg is None:
        if "profile" not in cfg:
            raise ValueError("provider or profile required")
        provider_cfg = {"type": "frozen_profile", "profile": cfg["profile"]}

    if not isinstance(provider_cfg, Mapping):
        raise ValueError("provider must be an object")

    provider_type = str(provider_cfg.get("type", "frozen_profile"))
    return registry.build(provider_type, provider_cfg)


def build_transport(
    cfg: Mapping[str, Any],
    registry: Registry[BaseTransport] = TRANSPORT_REGISTRY,
) -> BaseTransport:
    transport_cfg = cfg.get("transport", {"type": "memory"})
    if not isinstance(transport_cfg, Mapping):
        raise ValueError("transport must be an object")

    kind = str(transport_cfg.get("type", "memory"))
    return registry.build(kind, transport_cfg)


def build_clock(
    cfg: Mapping[str, Any],
    registry: Registry[BaseClock] = CLOCK_REGISTRY,
) -> BaseClock:
    clock_cfg = cfg.get("clock", {"type": "real"})
    if not isinstance(clock_cfg, Mapping):
        raise ValueError("clock must be an object")

    kind = str(clock_cfg.get("type", "real"))
    return registry.build(kind, clock_cfg)


def build_settings(cfg: Mapping[str, Any]) -> AgentSettings:
    duration = cfg.get("duration_seconds")
    settings = AgentSettings(
        interval_seconds=float(cfg.get("interval_seconds", 1.0)),
        duration_seconds=float(duration) if duration is not None else None,
        schema_version=int(cfg.get("schema_version", 1)),
        provider_health_timeout=float(cfg.get("provider_health_timeout", 5.0)),
    )
    settings.validate()
    return settings
