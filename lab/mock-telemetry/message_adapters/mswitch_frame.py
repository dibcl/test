from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from uuid import UUID

from .model import ProtocolMessage


# Existing definitions from prototype/mswitch_protocol.py and
# prototype/mswitch_frame_transport.py.
MAGIC = 0x5B5B5B5B
VERSION = 1
HEADER_SIZE = 0x80
MAX_MESSAGE_SIZE = 0xC800
SERIAL_ESCAPE = 0x5C
SERIAL_DELIMITER = 0x3B
FIXED_WIRE_PAYLOAD_SIZES = {4002: 512, 4004: 512}
ENVIRONMENT_SPACED_KEYS = ("diskused", "version", "targetversion")


class MswitchFrameError(ValueError):
    pass


def _environment_payload(payload: dict[str, object]) -> bytes:
    """Preserve the three spaces emitted by the observed VmQoEAgent 9050 writer."""
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=False,
    )
    for key in ENVIRONMENT_SPACED_KEYS:
        marker = f'"{key}":'
        if encoded.count(marker) != 1:
            raise MswitchFrameError(f"9050 payload must contain exactly one {key} field")
        encoded = encoded.replace(marker, marker + " ", 1)
    return encoded.encode("utf-8")


@dataclass(frozen=True, slots=True)
class MswitchHeader:
    magic: int
    version: int
    msgtype: int
    dst_type: int
    uuid: bytes
    src_mod: int
    dst_mod: int
    int_msgid: int
    data_len: int

    @classmethod
    def parse(cls, message: bytes) -> "MswitchHeader":
        if len(message) < HEADER_SIZE:
            raise MswitchFrameError(f"short Mswitch header: {len(message)}")
        header = cls(
            magic=struct.unpack_from("<I", message, 0x00)[0],
            version=struct.unpack_from("<I", message, 0x04)[0],
            msgtype=message[0x0C],
            dst_type=struct.unpack_from("<h", message, 0x22)[0],
            uuid=message[0x24:0x34],
            src_mod=struct.unpack_from("<I", message, 0x34)[0],
            dst_mod=struct.unpack_from("<I", message, 0x38)[0],
            int_msgid=struct.unpack_from("<I", message, 0x50)[0],
            data_len=struct.unpack_from("<I", message, 0x5C)[0],
        )
        if header.magic != MAGIC:
            raise MswitchFrameError(f"bad Mswitch magic: 0x{header.magic:08x}")
        if header.version != VERSION:
            raise MswitchFrameError(f"unsupported Mswitch version: {header.version}")
        if len(message) != HEADER_SIZE + header.data_len:
            raise MswitchFrameError(
                f"Mswitch length mismatch: actual={len(message)} "
                f"expected={HEADER_SIZE + header.data_len}"
            )
        return header


def decode_serial_frame(frame: bytes) -> bytes:
    if not frame or frame[-1] != SERIAL_DELIMITER:
        raise MswitchFrameError("Mswitch frame is missing its delimiter")
    message = bytearray()
    escaped = False
    for value in frame[:-1]:
        if escaped:
            message.append(value)
            escaped = False
        elif value == SERIAL_ESCAPE:
            escaped = True
        elif value == SERIAL_DELIMITER:
            raise MswitchFrameError("unescaped delimiter inside Mswitch frame")
        else:
            message.append(value)
    if escaped:
        raise MswitchFrameError("Mswitch frame ends after an escape byte")
    return bytes(message)


def _serial_frame(message: bytes) -> bytes:
    framed = bytearray()
    for value in message:
        if value in (SERIAL_ESCAPE, SERIAL_DELIMITER):
            framed.append(SERIAL_ESCAPE)
        framed.append(value)
    framed.append(SERIAL_DELIMITER)
    return bytes(framed)


def _version_payload(payload: dict[str, object]) -> bytes:
    keys = ("vmid", "vmbooster", "PVDriver", "vdagent", "usbipc", "media_redirect")
    values: dict[str, str] = {}
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, str) or "'" in value or any(ord(char) < 0x20 for char in value):
            raise MswitchFrameError(f"4004 {key} must be a safe string")
        values[key] = value
    return (
        "{msgtype:'4004',"
        f"vmid:'{values['vmid']}',"
        f"vmbooster:'{values['vmbooster']}',"
        "vmagent:' ',"
        f"PVDriver:'{values['PVDriver']}',"
        f"vdagent:'{values['vdagent']}',"
        f"usbipc:'{values['usbipc']}',"
        f"media_redirect:'{values['media_redirect']}'"
        "}"
    ).encode("ascii")


class MswitchFrameEncoder:
    def __init__(
        self,
        uuid: str | bytes,
        *,
        dst_type: int = 1,
        dst_type_by_module: dict[int, int] | None = None,
        msgtype_by_id: dict[int, int] | None = None,
    ) -> None:
        if isinstance(uuid, bytes):
            if len(uuid) != 16:
                raise MswitchFrameError("Mswitch UUID must be exactly 16 bytes")
            self.uuid16 = uuid
        else:
            try:
                self.uuid16 = UUID(uuid).bytes
            except (ValueError, AttributeError) as exc:
                raise MswitchFrameError("Mswitch UUID must be canonical") from exc
        self.dst_type = int(dst_type)
        self.dst_type_by_module = dict(dst_type_by_module or {})
        self.msgtype_by_id = dict(msgtype_by_id or {})

    @staticmethod
    def _payload(message: ProtocolMessage) -> bytes:
        if message.int_msgid == 4004:
            payload = _version_payload(message.payload)
        elif message.int_msgid == 9050:
            payload = _environment_payload(message.payload)
        else:
            payload = json.dumps(
                message.payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=False,
            ).encode("utf-8")
        fixed_size = FIXED_WIRE_PAYLOAD_SIZES.get(message.int_msgid)
        if fixed_size is not None:
            if len(payload) > fixed_size:
                raise MswitchFrameError(
                    f"{message.int_msgid} payload exceeds fixed {fixed_size}-byte size"
                )
            payload = payload.ljust(fixed_size, b"\x00")
        return payload

    def encode(self, message: ProtocolMessage) -> bytes:
        payload = self._payload(message)
        if HEADER_SIZE + len(payload) > MAX_MESSAGE_SIZE:
            raise MswitchFrameError("Mswitch message exceeds 0xc800 bytes")
        msgtype = self.msgtype_by_id.get(message.int_msgid, 0)
        if not 0 <= msgtype <= 0xFF:
            raise MswitchFrameError("Mswitch msgtype must fit in uint8")
        dst_type = self.dst_type_by_module.get(message.destination_module, self.dst_type)
        if not -0x8000 <= dst_type <= 0x7FFF:
            raise MswitchFrameError("Mswitch dst_type must fit in int16")

        header = bytearray(HEADER_SIZE)
        struct.pack_into("<II", header, 0x00, MAGIC, VERSION)
        header[0x0C] = msgtype
        struct.pack_into("<h", header, 0x22, dst_type)
        header[0x24:0x34] = self.uuid16
        struct.pack_into(
            "<II", header, 0x34, message.source_module, message.destination_module
        )
        struct.pack_into("<I", header, 0x50, message.int_msgid)
        struct.pack_into("<I", header, 0x5C, len(payload))
        return _serial_frame(bytes(header) + payload)
