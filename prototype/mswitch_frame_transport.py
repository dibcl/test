"""Test-only Mswitch framing and loopback transport.

The codec reuses the observed 0x80-byte header implementation. Network use is
deliberately restricted to loopback so test profiles cannot be sent to an
external Mswitch/Host endpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import ip_address
import json
import os
import socket
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import UUID

from mock_telemetry_agent import Envelope, MockTelemetryError
from mswitch_protocol import (
    Message,
    SerialFrameDecoder,
    build_message,
    encode_serial_frame,
    parse_message,
)
from vmbooster_payloads import VmboosterPayloadError, parse_4004
from ice_inside_protocol import IceInsideProtocolError, parse_8047_host_payload


TEST_ONLY_WIRE_KEYS = frozenset({"test_mode", "schema", "ack_expected"})
FIXED_WIRE_PAYLOAD_SIZES = {4002: 512, 4004: 512}


class MswitchFrameEncoder:
    """Encode/decode Mock envelopes using the observed Mswitch wire framing."""

    def __init__(
        self,
        test_uuid: str | bytes,
        *,
        dst_type: int = 0,
        dst_type_by_module: dict[int, int] | None = None,
        msgtype_by_id: dict[int, int] | None = None,
        test_mode: bool = False,
    ) -> None:
        if test_mode is not True:
            raise MockTelemetryError("Mswitch frame encoder requires test_mode=true")
        self.uuid16 = self._uuid_bytes(test_uuid)
        self.dst_type = int(dst_type)
        self.dst_type_by_module = dict(dst_type_by_module or {})
        self.msgtype_by_id = dict(msgtype_by_id or {})

    @staticmethod
    def _uuid_bytes(value: str | bytes) -> bytes:
        if isinstance(value, bytes):
            if len(value) != 16:
                raise MockTelemetryError("Mswitch UUID must contain 16 bytes")
            return value
        try:
            return UUID(value).bytes
        except (ValueError, AttributeError) as exc:
            raise MockTelemetryError("Mswitch UUID must be a canonical test UUID") from exc

    def encode(self, envelope: Envelope) -> bytes:
        if envelope.wire_payload is not None:
            payload = bytes(envelope.wire_payload)
        elif envelope.int_msgid == 0x8102C4:
            state = int(envelope.payload["raw_state"])
            if state not in (0, 1):
                raise MockTelemetryError("0x8102c4 raw_state must be 0 or 1")
            payload = bytes((state,))
        else:
            wire_value = {
                key: value for key, value in envelope.payload.items()
                if key not in TEST_ONLY_WIRE_KEYS
            }
            payload = json.dumps(
                wire_value,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=False,
            ).encode("utf-8")
        fixed_size = FIXED_WIRE_PAYLOAD_SIZES.get(envelope.int_msgid)
        if fixed_size is not None:
            if len(payload) > fixed_size:
                raise MockTelemetryError(
                    f"{envelope.int_msgid} payload exceeds fixed {fixed_size}-byte size"
                )
            payload = payload.ljust(fixed_size, b"\x00")
        message = build_message(
            dst_mod=envelope.destination_module,
            uuid=self.uuid16,
            dst_type=self.dst_type_by_module.get(envelope.destination_module, self.dst_type),
            int_msgid=envelope.int_msgid,
            payload=payload,
            msgtype=self.msgtype_by_id.get(envelope.int_msgid, 0),
            src_mod=envelope.source_module,
        )
        return encode_serial_frame(message.to_bytes())

    def decode(self, raw_frame: bytes) -> Envelope:
        message: Message = parse_message(raw_frame)
        wire_payload: bytes | None = None
        if message.int_msgid == 4004:
            visible_payload = message.payload.rstrip(b"\x00")
            try:
                decoded_4004 = parse_4004(visible_payload)
            except VmboosterPayloadError as exc:
                raise MockTelemetryError("invalid plaintext 4004 payload") from exc
            payload = {
                "test_mode": True,
                "schema": "vmbooster_4004_v1",
                **decoded_4004.as_payload(),
            }
            wire_payload = message.payload
        elif message.int_msgid == 8047:
            try:
                decoded_8047 = parse_8047_host_payload(message.payload)
            except IceInsideProtocolError as exc:
                raise MockTelemetryError("invalid plaintext 8047 payload") from exc
            payload = {
                "test_mode": True,
                "schema": "ice_inside_8047_v1",
                "msgtype": 8047,
                "msgid": decoded_8047.msgid,
                "vmuuid": decoded_8047.vmuuid,
                "ack_expected": False,
            }
            wire_payload = message.payload
        elif message.int_msgid == 0x8102C4:
            if message.payload not in (b"\x00", b"\x01"):
                raise MockTelemetryError("invalid 0x8102c4 one-byte state")
            payload: dict[str, Any] = {
                "test_mode": True,
                "raw_state": message.payload[0],
            }
        else:
            json_payload = message.payload
            if message.int_msgid in FIXED_WIRE_PAYLOAD_SIZES:
                json_payload = message.payload.rstrip(b"\x00")
                wire_payload = message.payload
            try:
                value = json.loads(json_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MockTelemetryError("Mswitch test payload is not JSON") from exc
            if not isinstance(value, dict):
                raise MockTelemetryError("decoded Mswitch test payload must be an object")
            payload = value
        return Envelope(
            int_msgid=message.int_msgid,
            source_module=message.src_mod,
            destination_module=message.dst_mod,
            emitted_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            payload=payload,
            wire_payload=wire_payload,
        )


class _MswitchStreamCore:
    """Shared short-write/fragmented-read logic for local test streams."""

    def __init__(
        self,
        encoder: MswitchFrameEncoder,
        read: Callable[[int], bytes],
        write: Callable[[bytes], int | None],
    ) -> None:
        self.encoder = encoder
        self._read = read
        self._write = write
        self._decoder = SerialFrameDecoder()

    def send(self, envelope: Envelope) -> None:
        wire = self.encoder.encode(envelope)
        offset = 0
        while offset < len(wire):
            written = self._write(wire[offset:])
            if written is None:
                written = len(wire) - offset
            if written <= 0:
                self._decoder.reset()
                raise MockTelemetryError("local Mswitch test stream made no write progress")
            offset += written

    def exchange(self, envelope: Envelope) -> list[Envelope]:
        self.send(envelope)
        while True:
            chunk = self._read(65536)
            if not chunk:
                self._decoder.reset()
                raise MockTelemetryError("local Mswitch test stream disconnected during response")
            frames = self._decoder.feed(chunk)
            if frames:
                return [self.encoder.decode(frame) for frame in frames]

    def reset_after_disconnect(self) -> None:
        self._decoder.reset()


class LocalStreamMswitchTransport:
    """Injectable local byte-stream adapter used for pipe boundary tests."""

    def __init__(self, stream: BinaryIO, test_uuid: str, *, test_mode: bool = False) -> None:
        self._stream = stream
        self._core = _MswitchStreamCore(
            MswitchFrameEncoder(test_uuid, test_mode=test_mode),
            stream.read,
            stream.write,
        )

    def send(self, envelope: Envelope) -> None:
        self._core.send(envelope)

    def exchange(self, envelope: Envelope) -> list[Envelope]:
        return self._core.exchange(envelope)

    def close(self) -> None:
        self._stream.close()


class LoopbackMswitchUnixTransport:
    """AF_UNIX provider restricted to explicitly named local test sockets."""

    def __init__(self, path: str, test_uuid: str, *, timeout: float = 3.0, test_mode: bool = False) -> None:
        socket_path = Path(path)
        if not socket_path.name.startswith("mswitch-test-"):
            raise MockTelemetryError("Unix test socket name must start with mswitch-test-")
        if not hasattr(socket, "AF_UNIX"):
            raise MockTelemetryError("AF_UNIX is unavailable on this platform")
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.settimeout(timeout)
        self._socket.connect(str(socket_path))
        self._core = _MswitchStreamCore(
            MswitchFrameEncoder(test_uuid, test_mode=test_mode),
            self._socket.recv,
            self._socket.send,
        )

    def send(self, envelope: Envelope) -> None:
        self._core.send(envelope)

    def exchange(self, envelope: Envelope) -> list[Envelope]:
        return self._core.exchange(envelope)

    def close(self) -> None:
        self._socket.close()


class LocalTestNamedPipeMswitchTransport(LocalStreamMswitchTransport):
    """Windows named-pipe provider restricted to zte-research test pipes."""

    PREFIX = r"\\.\pipe\zte-research-test-"

    def __init__(self, pipe_name: str, test_uuid: str, *, test_mode: bool = False) -> None:
        if os.name != "nt":
            raise MockTelemetryError("Windows named pipes are unavailable on this platform")
        if not pipe_name or any(value in pipe_name for value in ("\\", "/", ":")):
            raise MockTelemetryError("pipe_name must be a simple local test name")
        stream = open(self.PREFIX + pipe_name, "r+b", buffering=0)
        super().__init__(stream, test_uuid, test_mode=test_mode)


class LoopbackMswitchTcpTransport:
    """Mswitch binary framing over a loopback-only TCP test socket."""

    def __init__(
        self,
        host: str,
        port: int,
        test_uuid: str,
        *,
        timeout: float = 3.0,
        dst_type: int = 0,
        dst_type_by_module: dict[int, int] | None = None,
        msgtype_by_id: dict[int, int] | None = None,
        test_mode: bool = False,
    ) -> None:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        if not addresses or any(not ip_address(value).is_loopback for value in addresses):
            raise MockTelemetryError("loopback_mswitch_tcp only permits loopback destinations")
        if not 1 <= port <= 65535:
            raise MockTelemetryError("port must be between 1 and 65535")
        self.encoder = MswitchFrameEncoder(
            test_uuid,
            dst_type=dst_type,
            dst_type_by_module=dst_type_by_module,
            msgtype_by_id=msgtype_by_id,
            test_mode=test_mode,
        )
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._core = _MswitchStreamCore(self.encoder, self._socket.recv, self._socket.send)

    def send(self, envelope: Envelope) -> None:
        self._core.send(envelope)

    def exchange(self, envelope: Envelope) -> list[Envelope]:
        return self._core.exchange(envelope)

    def close(self) -> None:
        self._socket.close()
