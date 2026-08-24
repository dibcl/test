"""Strict codec for the statically confirmed IceDisplay 8047 text event."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID


class IceInsideProtocolError(ValueError):
    pass


_EVENT_8047 = re.compile(
    r"\Amsgtype=8047;msgdata=\{msgtype:'8047',msgid:'([0-9]+)',vmuuid:'([^']+)'\};\Z"
)
_HOST_8047 = re.compile(
    r"\A\{msgtype:'8047',msgid:'([0-9]+)',vmuuid:'([^']+)'\}\Z"
)


@dataclass(frozen=True)
class IceClientQuit8047:
    msgid: int
    vmuuid: str

    @property
    def msgtype(self) -> int:
        return 8047

    def encode(self) -> bytes:
        return build_8047(self.msgid, self.vmuuid)

    def encode_host_payload(self) -> bytes:
        return build_8047_host_payload(self.msgid, self.vmuuid)


def normalize_vmuuid(value: str) -> str:
    try:
        parsed = UUID(str(value))
    except (ValueError, AttributeError) as exc:
        raise IceInsideProtocolError("8047 vmuuid must be a canonical UUID") from exc
    canonical = str(parsed)
    if str(value).lower() != canonical:
        raise IceInsideProtocolError("8047 vmuuid must use canonical hyphenated form")
    return canonical


def build_8047(msgid: int, vmuuid: str) -> bytes:
    if isinstance(msgid, bool) or not isinstance(msgid, int) or not 0 <= msgid <= 0xFFFFFFFF:
        raise IceInsideProtocolError("8047 msgid must fit in uint32")
    canonical = normalize_vmuuid(vmuuid)
    return (
        "msgtype=8047;msgdata={msgtype:'8047',"
        f"msgid:'{msgid}',vmuuid:'{canonical}'"
        "};"
    ).encode("ascii")


def parse_8047(value: bytes | str) -> IceClientQuit8047:
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise IceInsideProtocolError("8047 event must be ASCII") from exc
    else:
        text = str(value)
    match = _EVENT_8047.fullmatch(text)
    if match is None:
        raise IceInsideProtocolError("8047 event does not match the confirmed IceDisplay schema")
    msgid = int(match.group(1), 10)
    if msgid > 0xFFFFFFFF:
        raise IceInsideProtocolError("8047 msgid exceeds uint32")
    return IceClientQuit8047(msgid, normalize_vmuuid(match.group(2)))


def build_8047_host_payload(msgid: int, vmuuid: str) -> bytes:
    if isinstance(msgid, bool) or not isinstance(msgid, int) or not 0 <= msgid <= 0xFFFFFFFF:
        raise IceInsideProtocolError("8047 msgid must fit in uint32")
    canonical = normalize_vmuuid(vmuuid)
    return (
        "{msgtype:'8047',"
        f"msgid:'{msgid}',vmuuid:'{canonical}'"
        "}"
    ).encode("ascii")


def parse_8047_host_payload(value: bytes | str) -> IceClientQuit8047:
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise IceInsideProtocolError("8047 Host payload must be ASCII") from exc
    else:
        text = str(value)
    match = _HOST_8047.fullmatch(text)
    if match is None:
        raise IceInsideProtocolError("8047 Host payload does not match the observed schema")
    msgid = int(match.group(1), 10)
    if msgid > 0xFFFFFFFF:
        raise IceInsideProtocolError("8047 msgid exceeds uint32")
    return IceClientQuit8047(msgid, normalize_vmuuid(match.group(2)))
