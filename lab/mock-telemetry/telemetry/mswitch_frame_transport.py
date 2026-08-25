from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import socket
from collections.abc import Mapping
from typing import Any

from message_adapters.model import ProtocolMessage
from message_adapters.mswitch_frame import (
    HEADER_SIZE,
    MAX_MESSAGE_SIZE,
    SERIAL_DELIMITER,
    SERIAL_ESCAPE,
    MswitchFrameEncoder,
    MswitchHeader,
)

from .transports import BaseTransport, NetworkPolicy


def _integer_map(value: Any, label: str) -> dict[int, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    result: dict[int, int] = {}
    for key, item in value.items():
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{label} values must be integers")
        try:
            result[int(key)] = item
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} keys must be integer text") from exc
    return result


def _protocol_message(value: dict[str, Any]) -> ProtocolMessage:
    required = {
        "int_msgid",
        "source_module",
        "destination_module",
        "emitted_at",
        "payload",
    }
    if set(value) != required:
        raise ValueError("protocol message fields are incomplete or contain extras")
    integer_keys = ("int_msgid", "source_module", "destination_module")
    if any(
        isinstance(value[key], bool) or not isinstance(value[key], int)
        for key in integer_keys
    ):
        raise ValueError("protocol message ids and modules must be integers")
    if not isinstance(value["emitted_at"], str) or not isinstance(value["payload"], dict):
        raise ValueError("protocol message timestamp/payload types are invalid")
    return ProtocolMessage(
        int_msgid=value["int_msgid"],
        source_module=value["source_module"],
        destination_module=value["destination_module"],
        emitted_at=value["emitted_at"],
        payload=value["payload"],
    )


class MswitchFrameTransport(BaseTransport):
    MODES = frozenset({"json_debug", "mswitch"})

    def __init__(
        self,
        host: str,
        port: int,
        uuid: str,
        *,
        mode: str = "json_debug",
        timeout: float = 5.0,
        dst_type: int = 1,
        dst_type_by_module: Mapping[Any, Any] | None = None,
        msgtype_by_id: Mapping[Any, Any] | None = None,
        ack_by_request: Mapping[Any, Any] | None = None,
    ) -> None:
        if mode not in self.MODES:
            raise ValueError(f"unknown Mswitch output mode: {mode!r}")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("Mswitch port must be between 1 and 65535")
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise ValueError("Mswitch timeout must be positive")
        self.host = host
        self.port = port
        self.mode = mode
        self.timeout = float(timeout)
        self.policy = NetworkPolicy(allow_public=False)
        self.encoder = MswitchFrameEncoder(
            uuid,
            dst_type=dst_type,
            dst_type_by_module=_integer_map(dst_type_by_module, "dst_type_by_module"),
            msgtype_by_id=_integer_map(msgtype_by_id, "msgtype_by_id"),
        )
        self.ack_by_request = (
            {4002: 4100}
            if ack_by_request is None
            else _integer_map(ack_by_request, "ack_by_request")
        )
        self.acknowledgements: list[ProtocolMessage] = []
        self._read_buffer = bytearray()
        self._read_escaped = False
        self.reader = None
        self.writer = None

    def _feed_frames(self, chunk: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for value in chunk:
            if self._read_escaped:
                self._read_buffer.append(value)
                self._read_escaped = False
            elif value == SERIAL_ESCAPE:
                self._read_escaped = True
            elif value == SERIAL_DELIMITER:
                frames.append(bytes(self._read_buffer))
                self._read_buffer.clear()
            else:
                self._read_buffer.append(value)
            if len(self._read_buffer) > MAX_MESSAGE_SIZE:
                self._read_buffer.clear()
                self._read_escaped = False
                raise ValueError("received Mswitch frame exceeds 0xc800 bytes")
        return frames

    @staticmethod
    def _decode_ack(raw: bytes, emitted_at: str) -> ProtocolMessage:
        header = MswitchHeader.parse(raw)
        payload_bytes = raw[HEADER_SIZE:].rstrip(b"\x00")
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Mswitch ACK payload is not JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Mswitch ACK payload must be an object")
        return ProtocolMessage(
            int_msgid=header.int_msgid,
            source_module=header.src_mod,
            destination_module=header.dst_mod,
            emitted_at=emitted_at,
            payload=payload,
        )

    async def _receive_ack(
        self,
        request: ProtocolMessage,
        expected_msgid: int,
    ) -> ProtocolMessage:
        if self.reader is None:
            raise RuntimeError("transport reader not opened")
        while True:
            chunk = await asyncio.wait_for(self.reader.read(65536), self.timeout)
            if not chunk:
                self._read_buffer.clear()
                self._read_escaped = False
                raise ConnectionError("mock Mswitch host closed before ACK")
            for raw in self._feed_frames(chunk):
                acknowledgement = self._decode_ack(raw, request.emitted_at)
                self.acknowledgements.append(acknowledgement)
                if acknowledgement.int_msgid != expected_msgid:
                    continue
                if acknowledgement.source_module != request.destination_module:
                    raise ValueError("Mswitch ACK source module does not match request")
                if acknowledgement.destination_module != request.source_module:
                    raise ValueError("Mswitch ACK destination module does not match request")
                return acknowledgement

    async def open(self) -> None:
        infos = await self.policy.resolve(self.host, self.port, socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(info[4][0]).is_loopback for info in infos):
            raise ValueError("Mswitch frame transport only permits loopback destinations")
        loop = asyncio.get_running_loop()
        last_error: BaseException | None = None
        for family, socktype, proto, _, sockaddr in infos:
            sock = socket.socket(family, socktype, proto)
            sock.setblocking(False)
            try:
                await asyncio.wait_for(loop.sock_connect(sock, sockaddr), self.timeout)
                self.reader, self.writer = await asyncio.open_connection(sock=sock)
                return
            except BaseException as exc:
                last_error = exc
                sock.close()
                if isinstance(exc, asyncio.CancelledError):
                    raise
        raise OSError("unable to connect to loopback Mswitch test endpoint") from last_error

    async def send(self, message: dict[str, Any]) -> None:
        if self.writer is None:
            raise RuntimeError("transport not opened")
        protocol_message: ProtocolMessage | None = None
        if self.mode == "json_debug":
            output = json.dumps(
                message, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8") + b"\n"
        else:
            protocol_message = _protocol_message(message)
            output = self.encoder.encode(protocol_message)
        self.writer.write(output)
        await self.writer.drain()
        if protocol_message is not None:
            expected_ack = self.ack_by_request.get(protocol_message.int_msgid)
            if expected_ack is not None:
                await self._receive_ack(protocol_message, expected_ack)

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
            self.writer = None
            self.reader = None
        self._read_buffer.clear()
        self._read_escaped = False
