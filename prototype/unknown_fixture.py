"""Evidence-preserving recorder for unsupported local protocol fixtures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Callable

from mswitch_protocol import Message, parse_message


@dataclass(frozen=True)
class UnknownFixture:
    status: str
    int_msgid: int
    header_hex: str
    payload_length: int
    payload_sha256: str
    redacted_payload: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class UnknownFixtureRecorder:
    """Record metadata without inventing an acknowledgement or response."""

    def __init__(self, redactor: Callable[[bytes], str] | None = None) -> None:
        self.redactor = redactor or (lambda _: "<redacted>")
        self.records: list[UnknownFixture] = []

    def record_frame(self, raw_frame: bytes) -> UnknownFixture:
        message: Message = parse_message(raw_frame)
        record = UnknownFixture(
            status="unsupported_fixture",
            int_msgid=message.int_msgid,
            header_hex=message.raw_header.hex(),
            payload_length=len(message.payload),
            payload_sha256=sha256(message.payload).hexdigest(),
            redacted_payload=self.redactor(message.payload),
        )
        self.records.append(record)
        return record
