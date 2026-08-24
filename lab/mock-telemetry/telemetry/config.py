from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent import AgentSettings
from .clocks import RealClock, SimulatedClock
from .providers import FrozenProfileProvider, LiveSystemProvider, SyntheticMetricsProvider
from .transports import FileDumpTransport, MemoryTransport, TcpTransport, UdpTransport


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fp:
        return json.load(fp)


def build_provider(cfg: dict[str, Any]):
    provider_cfg = cfg.get("provider")

    # Backward compatibility with existing profile-only configs.
    if provider_cfg is None:
        if "profile" not in cfg:
            raise ValueError("provider or profile required")
        return FrozenProfileProvider(cfg["profile"])

    provider_type = provider_cfg.get("type", "frozen_profile")
    if provider_type == "frozen_profile":
        return FrozenProfileProvider(provider_cfg["profile"])
    if provider_type == "synthetic":
        return SyntheticMetricsProvider(provider_cfg["profile"])
    if provider_type == "live_system":
        return LiveSystemProvider(process_limit=int(provider_cfg.get("process_limit", 20)))
    raise ValueError(f"unknown provider: {provider_type}")


def build_transport(cfg: dict[str, Any]):
    transport_cfg = cfg.get("transport", {"type": "memory"})
    kind = transport_cfg.get("type", "memory")

    if kind == "memory":
        return MemoryTransport()
    if kind == "file_dump":
        return FileDumpTransport(transport_cfg["path"])
    if kind in {"tcp", "loopback_tcp"}:
        return TcpTransport(
            host=transport_cfg.get("host", "127.0.0.1"),
            port=int(transport_cfg["port"]),
            timeout=float(transport_cfg.get("timeout", 5.0)),
            allow_public=bool(transport_cfg.get("allow_public", False)),
        )
    if kind == "udp":
        return UdpTransport(
            host=transport_cfg.get("host", "127.0.0.1"),
            port=int(transport_cfg["port"]),
            allow_public=bool(transport_cfg.get("allow_public", False)),
        )
    raise ValueError(f"unknown transport: {kind}")


def build_clock(cfg: dict[str, Any]):
    clock_cfg = cfg.get("clock", {"type": "real"})
    kind = clock_cfg.get("type", "real")

    if kind == "real":
        return RealClock()
    if kind == "simulated":
        raw_start = clock_cfg.get("start")
        start = datetime.fromisoformat(raw_start) if raw_start else None
        return SimulatedClock(start)
    raise ValueError(f"unknown clock: {kind}")


def build_settings(cfg: dict[str, Any]) -> AgentSettings:
    duration = cfg.get("duration_seconds")
    return AgentSettings(
        interval_seconds=float(cfg.get("interval_seconds", 1.0)),
        duration_seconds=float(duration) if duration is not None else None,
        schema_version=int(cfg.get("schema_version", 1)),
    )
