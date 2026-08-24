"""Offline-only codec for the observed libmswitch 0x80-byte message format.

This module deliberately contains no socket, serial-port, or process-control
code.  Unknown header bytes are preserved when parsing and cloning messages.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct


MAGIC = 0x5B5B5B5B
VERSION = 1
HEADER_SIZE = 0x80
MAX_MESSAGE_SIZE = 0xC800
REGISTER_MSG_ID = 0x20130223
SERIAL_ESCAPE = 0x5C
SERIAL_DELIMITER = 0x3B


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Message:
    raw_header: bytes
    payload: bytes

    @property
    def magic(self) -> int:
        return struct.unpack_from("<I", self.raw_header, 0x00)[0]

    @property
    def version(self) -> int:
        return struct.unpack_from("<I", self.raw_header, 0x04)[0]

    @property
    def msgtype(self) -> int:
        return self.raw_header[0x0C]

    @property
    def dst_type(self) -> int:
        return struct.unpack_from("<h", self.raw_header, 0x22)[0]

    @property
    def uuid(self) -> bytes:
        return self.raw_header[0x24:0x34]

    @property
    def src_mod(self) -> int:
        return struct.unpack_from("<I", self.raw_header, 0x34)[0]

    @property
    def dst_mod(self) -> int:
        return struct.unpack_from("<I", self.raw_header, 0x38)[0]

    @property
    def int_msgid(self) -> int:
        return struct.unpack_from("<I", self.raw_header, 0x50)[0]

    @property
    def data_len(self) -> int:
        return struct.unpack_from("<I", self.raw_header, 0x5C)[0]

    def to_bytes(self) -> bytes:
        header = bytearray(self.raw_header)
        struct.pack_into("<I", header, 0x5C, len(self.payload))
        return bytes(header) + self.payload


def parse_message(data: bytes) -> Message:
    if len(data) < HEADER_SIZE:
        raise ProtocolError(f"short header: {len(data)}")
    if len(data) > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"message exceeds 0xc800 bytes: {len(data)}")
    magic, version = struct.unpack_from("<II", data, 0)
    if magic != MAGIC:
        raise ProtocolError(f"bad magic: 0x{magic:08x}")
    if version != VERSION:
        raise ProtocolError(f"unsupported version: {version}")
    data_len = struct.unpack_from("<I", data, 0x5C)[0]
    expected = HEADER_SIZE + data_len
    if len(data) != expected:
        raise ProtocolError(f"length mismatch: actual={len(data)} expected={expected}")
    return Message(bytes(data[:HEADER_SIZE]), bytes(data[HEADER_SIZE:]))


def build_message(
    *,
    dst_mod: int,
    uuid: bytes,
    dst_type: int,
    int_msgid: int,
    payload: bytes = b"",
    msgtype: int = 0,
    src_mod: int = 0,
) -> Message:
    if len(uuid) != 16:
        raise ProtocolError("uuid must be exactly 16 bytes")
    if not 0 <= msgtype <= 0xFF:
        raise ProtocolError("msgtype must fit in uint8")
    if HEADER_SIZE + len(payload) > MAX_MESSAGE_SIZE:
        raise ProtocolError("message exceeds 0xc800 bytes")
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<II", header, 0x00, MAGIC, VERSION)
    header[0x0C] = msgtype
    struct.pack_into("<h", header, 0x22, dst_type)
    header[0x24:0x34] = uuid
    struct.pack_into("<II", header, 0x34, src_mod, dst_mod)
    struct.pack_into("<I", header, 0x50, int_msgid)
    struct.pack_into("<I", header, 0x5C, len(payload))
    return Message(bytes(header), bytes(payload))


def build_register_request(local_mod: int) -> Message:
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<II", header, 0x00, MAGIC, VERSION)
    struct.pack_into("<I", header, 0x34, local_mod)
    struct.pack_into("<I", header, 0x50, REGISTER_MSG_ID)
    payload = struct.pack("<I", local_mod)
    struct.pack_into("<I", header, 0x5C, len(payload))
    return Message(bytes(header), payload)


def parse_register_response(data: bytes) -> bytes:
    message = parse_message(data)
    if message.int_msgid != REGISTER_MSG_ID:
        raise ProtocolError("not a register response")
    if message.msgtype != 1:
        raise ProtocolError("register response msgtype is not 1")
    if len(message.payload) != 16:
        raise ProtocolError("register response UUID is not 16 bytes")
    return message.payload


def encode_serial_frame(message: bytes) -> bytes:
    """Apply the MswitchWin vport byte-stuffing and trailing delimiter."""
    if len(message) > MAX_MESSAGE_SIZE:
        raise ProtocolError("serial message exceeds 0xc800 bytes")
    framed = bytearray()
    for value in message:
        if value in (SERIAL_ESCAPE, SERIAL_DELIMITER):
            framed.append(SERIAL_ESCAPE)
        framed.append(value)
    framed.append(SERIAL_DELIMITER)
    return bytes(framed)


class SerialFrameDecoder:
    """Incrementally decode arbitrary VirtIO Serial read chunks."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._escaped = False

    def feed(self, chunk: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for value in chunk:
            if self._escaped:
                self._buffer.append(value)
                self._escaped = False
            elif value == SERIAL_ESCAPE:
                self._escaped = True
            elif value == SERIAL_DELIMITER:
                frames.append(bytes(self._buffer))
                self._buffer.clear()
            else:
                self._buffer.append(value)
            if len(self._buffer) > MAX_MESSAGE_SIZE:
                self._buffer.clear()
                self._escaped = False
                raise ProtocolError("decoded serial frame exceeds 0xc800 bytes")
        return frames

    def finish(self) -> None:
        """Validate that a finite test stream ended on a frame boundary."""
        if self._escaped:
            raise ProtocolError("serial stream ended after an escape byte")
        if self._buffer:
            raise ProtocolError("serial stream ended before a frame delimiter")

    def reset(self) -> None:
        """Discard an incomplete frame after a test-stream disconnect."""
        self._buffer.clear()
        self._escaped = False
