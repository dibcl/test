from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .model import MessageAdapter, ProtocolMessage
from .mswitch_frame import MswitchFrameEncoder
from .scheduler import TelemetryMessageScheduler
from .windows import WindowsMessageEncoder


def build_message_adapter(config: Mapping[str, Any]) -> MessageAdapter | None:
    raw = config.get("message_adapter")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("message_adapter must be an object")
    adapter_type = raw.get("type")
    if adapter_type != "windows_protocol":
        raise ValueError(f"unknown message adapter: {adapter_type!r}")
    schedule = raw.get("schedule", {})
    if not isinstance(schedule, Mapping):
        raise ValueError("message_adapter.schedule must be an object")
    return TelemetryMessageScheduler(WindowsMessageEncoder(raw), schedule)


__all__ = [
    "MessageAdapter",
    "ProtocolMessage",
    "MswitchFrameEncoder",
    "TelemetryMessageScheduler",
    "WindowsMessageEncoder",
    "build_message_adapter",
]
