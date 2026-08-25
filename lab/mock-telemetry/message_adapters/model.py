from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from telemetry.model import TelemetrySnapshot


@dataclass(frozen=True, slots=True)
class ProtocolMessage:
    int_msgid: int
    source_module: int
    destination_module: int
    emitted_at: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "int_msgid": self.int_msgid,
            "source_module": self.source_module,
            "destination_module": self.destination_module,
            "emitted_at": self.emitted_at,
            "payload": self.payload,
        }


class MessageAdapter(Protocol):
    def messages_for(
        self,
        snapshot: TelemetrySnapshot,
        elapsed_seconds: float,
    ) -> list[ProtocolMessage]: ...
