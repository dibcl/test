"""Strict codecs for statically confirmed Vmbooster plaintext payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re


class VmboosterPayloadError(ValueError):
    pass


_PAYLOAD_4004 = re.compile(
    r"\A\{msgtype:'4004',vmid:'([^']*)',vmbooster:'([^']*)',vmagent:' ',"
    r"PVDriver:'([^']*)',vdagent:'([^']*)',usbipc:'([^']*)',"
    r"media_redirect:'([^']*)'\}\Z"
)


@dataclass(frozen=True)
class VmVersion4004:
    vmid: str
    vmbooster: str
    PVDriver: str
    vdagent: str
    usbipc: str
    media_redirect: str

    def as_payload(self) -> dict[str, str]:
        return {
            "msgtype": "4004",
            **asdict(self),
            "vmagent": " ",
        }

    def encode(self) -> bytes:
        return build_4004(**asdict(self))


def _field(name: str, value: str, *, allow_empty: bool = False) -> str:
    text = str(value)
    if (not text and not allow_empty) or "'" in text or any(ord(char) < 0x20 for char in text):
        raise VmboosterPayloadError(f"4004 {name} must be a safe string")
    return text


def build_4004(
    *,
    vmid: str,
    vmbooster: str,
    PVDriver: str,
    vdagent: str,
    usbipc: str,
    media_redirect: str,
) -> bytes:
    values = {
        "vmid": _field("vmid", vmid),
        "vmbooster": _field("vmbooster", vmbooster),
        "PVDriver": _field("PVDriver", PVDriver, allow_empty=True),
        "vdagent": _field("vdagent", vdagent, allow_empty=True),
        "usbipc": _field("usbipc", usbipc, allow_empty=True),
        "media_redirect": _field("media_redirect", media_redirect, allow_empty=True),
    }
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


def parse_4004(value: bytes | str) -> VmVersion4004:
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise VmboosterPayloadError("4004 payload must be ASCII") from exc
    else:
        text = str(value)
    match = _PAYLOAD_4004.fullmatch(text)
    if match is None:
        raise VmboosterPayloadError("4004 payload does not match the confirmed PE format")
    return VmVersion4004(*match.groups())
