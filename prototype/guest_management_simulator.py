"""Pure in-memory Guest-management simulator for offline protocol tests.

There is intentionally no socket, VirtIO, service-control, or process-control
code in this module.  It exercises the observed message codec and state
transitions against a fake Host only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import hashlib
import json
import re
import struct
from urllib.parse import unquote_plus

from mswitch_protocol import (
    REGISTER_MSG_ID,
    Message,
    ProtocolError,
    build_message,
    build_register_request,
    parse_register_response,
)


VMBOOSTER_MOD = 0x80000001
HOST_MOD = 0x80000000
AUX_HOST_MOD = 6

HEARTBEAT_ID = 4002
VM_INFO_REQUEST_ID = 8008
NETWORK_REPORT_ID = 0x8102BF
NETWORK_ACK_ID = 0x8102C1
OS_REPORT_ID = 0x8102C5
OS_ACK_ID = 0x8102C7
IP_INFO_REQUEST_ID = 9011
IP_INFO_RESPONSE_ID = 9012


class SimulatorState(Enum):
    BOOT = auto()
    REGISTERED = auto()
    IDENTIFIED = auto()
    HEALTHY = auto()


@dataclass(frozen=True)
class CurrentBaseline:
    """Latest environment/inventory snapshot observed in local vmswitch.log."""

    source_uuid: str
    environment: dict[str, object]
    software: tuple[dict[str, object], ...]

    @property
    def offline_uuid(self) -> bytes:
        """Derive a non-production UUID for the in-memory fake Host."""
        seed = (self.source_uuid + "\0zte-offline-simulator").encode("utf-8")
        return hashlib.sha256(seed).digest()[:16]

    def public_summary(self) -> dict[str, object]:
        environment = dict(self.environment)
        for key in ("mac", "ip", "computername"):
            if key in environment:
                environment[key] = "<redacted>"
        return {
            "environment": environment,
            "software_count": len(self.software),
            "offline_uuid_sha256": hashlib.sha256(self.offline_uuid).hexdigest(),
        }


def _extract_json_payload(line: str, msgid: int) -> dict[str, object] | None:
    marker = f"int_msgid={msgid},"
    if marker not in line or "msg=" not in line:
        return None
    payload = line.split("msg=", 1)[1].strip()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def load_current_baseline(vmswitch_log: str) -> CurrentBaseline:
    """Load only the most recent boot snapshot from the current local log.

    The latest 9050 environment message defines the start of the snapshot.
    Consecutive 9054 inventory batches following it are collected until the
    first 9051 performance batch.  Instance material is never written out.
    """
    with open(vmswitch_log, "r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()

    latest_environment_index = -1
    latest_environment: dict[str, object] | None = None
    for index, line in enumerate(lines):
        payload = _extract_json_payload(line, 9050)
        if payload is not None:
            latest_environment_index = index
            latest_environment = payload
    if latest_environment is None:
        raise ProtocolError("no 9050 environment snapshot found")

    source_uuid = str(latest_environment.get("uuid", ""))
    if not source_uuid:
        raise ProtocolError("latest 9050 snapshot has no UUID")
    raw_environment = latest_environment.get("environment")
    if not isinstance(raw_environment, dict):
        raise ProtocolError("latest 9050 snapshot has no environment object")
    environment = {
        str(key): unquote_plus(str(value)) if isinstance(value, str) else value
        for key, value in raw_environment.items()
    }

    software: list[dict[str, object]] = []
    for line in lines[latest_environment_index + 1 :]:
        if _extract_json_payload(line, 9051) is not None:
            break
        payload = _extract_json_payload(line, 9054)
        if payload is None:
            continue
        entries = payload.get("softwares", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                decoded = {
                    str(key): unquote_plus(str(value)) if isinstance(value, str) else value
                    for key, value in entry.items()
                }
                software.append(decoded)
    return CurrentBaseline(source_uuid, environment, tuple(software))


def _payload_text(message: Message) -> str:
    return message.payload.decode("utf-8", errors="strict")


def _extract_decimal_token(text: str, prefix: str) -> str:
    match = re.search(re.escape(prefix) + r"(\d+)", text)
    if not match:
        raise ProtocolError(f"missing dynamic token after {prefix!r}")
    return match.group(1)


class FakeHost:
    """Small deterministic Host oracle used only by unit/integration tests."""

    def __init__(self, offline_uuid: bytes) -> None:
        if len(offline_uuid) != 16:
            raise ProtocolError("offline UUID must be 16 bytes")
        self.uuid = offline_uuid
        self.received: list[Message] = []

    def exchange(self, message: Message) -> list[Message]:
        self.received.append(message)
        if message.int_msgid == REGISTER_MSG_ID:
            local_mod = struct.unpack("<I", message.payload)[0]
            response = build_message(
                dst_mod=local_mod,
                uuid=b"\0" * 16,
                dst_type=0,
                int_msgid=REGISTER_MSG_ID,
                payload=self.uuid,
                msgtype=1,
            )
            return [response]
        if message.int_msgid == VM_INFO_REQUEST_ID:
            payload = f"msgtype=8009;vuuid={self.uuid.hex()};".encode()
            return [self._to_guest(0, payload)]
        if message.int_msgid == HEARTBEAT_ID:
            payload = f"[performance];msgtype=4100;vmuuid={self.uuid.hex()};".encode()
            return [self._to_guest(0, payload)]
        if message.int_msgid == NETWORK_REPORT_ID:
            return [self._to_guest(NETWORK_ACK_ID, message.payload)]
        if message.int_msgid == OS_REPORT_ID:
            return [self._to_guest(OS_ACK_ID, message.payload)]
        if message.int_msgid == IP_INFO_REQUEST_ID:
            token = _extract_decimal_token(_payload_text(message), "getipinfo")
            payload = (
                'msgtype=9012;{"vmuuid":"offline","huuid":"offline",'
                f'"msgid":"getipinfo{token}","DCIPV6":"","DC":"",}}'
            ).encode()
            return [self._to_guest(IP_INFO_RESPONSE_ID, payload)]
        return []

    def _to_guest(self, int_msgid: int, payload: bytes) -> Message:
        return build_message(
            dst_mod=VMBOOSTER_MOD,
            uuid=self.uuid,
            dst_type=2,
            int_msgid=int_msgid,
            payload=payload,
            src_mod=HOST_MOD,
        )


class OfflineGuestSimulator:
    """Minimal observed Vmbooster state machine backed by a fake Host."""

    def __init__(self, baseline: CurrentBaseline, host: FakeHost) -> None:
        self.baseline = baseline
        self.host = host
        self.state = SimulatorState.BOOT
        self.uuid = b"\0" * 16
        self.heartbeat_count = 0

    def register(self) -> None:
        responses = self.host.exchange(build_register_request(VMBOOSTER_MOD))
        if len(responses) != 1:
            raise ProtocolError("fake Host did not return one register response")
        self.uuid = parse_register_response(responses[0].to_bytes())
        self.state = SimulatorState.REGISTERED

    def identify(self) -> None:
        self._require(SimulatorState.REGISTERED)
        responses = self.host.exchange(self._message(HOST_MOD, VM_INFO_REQUEST_ID, b"msgtype:'8008'"))
        if len(responses) != 1 or "msgtype=8009" not in _payload_text(responses[0]):
            raise ProtocolError("invalid VM information response")
        self.state = SimulatorState.IDENTIFIED

    def report_baseline(self) -> None:
        self._require(SimulatorState.IDENTIFIED)
        env = self.baseline.environment
        network = (
            f"ip={env.get('ip', '')},mac={env.get('mac', '')},"
            "gateway=offline,netmask=offline,dns=offline,dhcp=1;"
        ).encode()
        network_responses = self.host.exchange(
            self._message(AUX_HOST_MOD, NETWORK_REPORT_ID, network)
        )
        if [item.int_msgid for item in network_responses] != [NETWORK_ACK_ID]:
            raise ProtocolError("network report was not acknowledged")

        os_payload = (
            f"OsName={env.get('os', '')};Osbit={1 if str(env.get('bit')) == '64' else 0};"
            "ReslutFlag=1;"
        ).encode()
        os_responses = self.host.exchange(
            self._message(AUX_HOST_MOD, OS_REPORT_ID, os_payload)
        )
        if [item.int_msgid for item in os_responses] != [OS_ACK_ID]:
            raise ProtocolError("OS report was not acknowledged")
        self.state = SimulatorState.HEALTHY

    def heartbeat(self) -> None:
        self._require(SimulatorState.HEALTHY)
        payload = json.dumps(
            {
                "msgtype": "4002",
                "agentversion": self.baseline.environment.get("version", ""),
                "vmid": "offline",
                "agentstatus": "1",
                "computername": self.baseline.environment.get("computername", ""),
                "issysprep": "0",
            },
            separators=(",", ":"),
        ).encode()
        responses = self.host.exchange(self._message(HOST_MOD, HEARTBEAT_ID, payload))
        if len(responses) != 1 or "msgtype=4100" not in _payload_text(responses[0]):
            raise ProtocolError("heartbeat was not acknowledged")
        self.heartbeat_count += 1

    def request_ip_info(self, token: int) -> None:
        self._require(SimulatorState.HEALTHY)
        responses = self.host.exchange(
            self._message(HOST_MOD, IP_INFO_REQUEST_ID, f"getipinfo{token}".encode())
        )
        if len(responses) != 1 or responses[0].int_msgid != IP_INFO_RESPONSE_ID:
            raise ProtocolError("IP information request was not answered")
        echoed = _extract_decimal_token(_payload_text(responses[0]), "getipinfo")
        if echoed != str(token):
            raise ProtocolError("IP information response token mismatch")

    def run_startup(self) -> None:
        self.register()
        self.identify()
        self.report_baseline()

    def _message(self, dst_mod: int, int_msgid: int, payload: bytes) -> Message:
        return build_message(
            dst_mod=dst_mod,
            uuid=self.uuid,
            dst_type=1,
            int_msgid=int_msgid,
            payload=payload,
            src_mod=VMBOOSTER_MOD,
        )

    def _require(self, expected: SimulatorState) -> None:
        if self.state is not expected:
            raise ProtocolError(f"state is {self.state.name}, expected {expected.name}")

