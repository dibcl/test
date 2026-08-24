"""Bidirectional test-only Guest management state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol

from mock_telemetry_agent import Envelope
from ice_inside_protocol import IceClientQuit8047, build_8047, parse_8047
from vmbooster_payloads import VmVersion4004


VMBOOSTER = 0x80000001
HOST = 0x80000000
AUX_HOST = 6


class ExchangeTransport(Protocol):
    def send(self, envelope: Envelope) -> None: ...

    def exchange(self, envelope: Envelope) -> list[Envelope]: ...


class SessionState(Enum):
    NEW = auto()
    VM_INFO_PENDING = auto()
    VM_IDENTIFIED = auto()
    BASELINE_PENDING = auto()
    HEALTHY = auto()
    RENEGOTIATING = auto()
    DEGRADED = auto()


@dataclass
class AckCounters:
    vm_info: int = 0
    mac: int = 0
    network: int = 0
    os: int = 0
    csap: int = 0
    ip_info: int = 0
    heartbeat: int = 0


class BidirectionalGuestSession:
    def __init__(self, transport: ExchangeTransport, identity: dict[str, Any], environment: dict[str, Any]) -> None:
        if identity.get("test_mode") is not True:
            raise ValueError("bidirectional simulator requires test_mode=true")
        self.transport = transport
        self.identity = identity
        self.environment = environment
        module_ids = dict(identity.get("module_ids", {}))
        self.vmbooster_module = int(module_ids.get("vmbooster", VMBOOSTER))
        self.host_module = int(module_ids.get("host", HOST))
        self.aux_host_module = int(module_ids.get("aux_host", AUX_HOST))
        self.state = SessionState.NEW
        self.acks = AckCounters()
        self.heartbeat_sequence = 0
        self.missed_heartbeat_acks = 0
        self.max_missed_heartbeat_acks = int(identity.get("max_missed_heartbeat_acks", 3))
        self.heartbeat_extensions = bool(identity.get("heartbeat_extensions", True))
        self.request_sequence = 0
        self.session_uuid = ""
        self.mac_uuid = ""
        self.csap_endpoint: tuple[str, int] | None = None
        self.locked = False
        self.lock_transition_sequence = 0
        self.aux_state_sequence = 0
        self.ice_client_connected = True
        self.last_client_quit: IceClientQuit8047 | None = None

    def start(self, emitted_at: str) -> None:
        self.state = SessionState.VM_INFO_PENDING
        responses = self.transport.exchange(self._envelope(8008, self.host_module, emitted_at, {
            "test_mode": True,
            "msgtype": "8008",
        }))
        self._handle_responses(responses)
        if self.state is not SessionState.VM_IDENTIFIED:
            self.state = SessionState.DEGRADED
            raise RuntimeError("8009 VM information response missing")

        self.state = SessionState.BASELINE_PENDING
        self._handle_responses(self.transport.exchange(self._envelope(1300, self.host_module, emitted_at, {
            "test_mode": True,
            "msgtype": "1300",
            "macid": self.environment["mac"],
        })))
        network = {
            "test_mode": True,
            "ip": self.environment["ip"],
            "gateway": self.environment["gateway"],
            "netmask": self.environment["netmask"],
            "mac": self.environment["mac"],
            "dns": self.environment["dns"],
            "dhcp": self.environment["dhcp"],
        }
        self._handle_responses(self.transport.exchange(self._envelope(0x8102BF, self.aux_host_module, emitted_at, network)))
        os_report = {
            "test_mode": True,
            "os_name": self.environment["os"],
            "os_bit": self.environment["bit"],
            "result": 1,
        }
        self._handle_responses(self.transport.exchange(self._envelope(0x8102C5, self.aux_host_module, emitted_at, os_report)))
        self.refresh_csap(emitted_at)
        self.request_ip_info(emitted_at)
        versions = dict(self.identity["software_versions"])
        version_event = VmVersion4004(
            vmid=str(self.identity["test_vmid"]),
            vmbooster=str(versions["vmbooster"]),
            PVDriver=str(versions["PVDriver"]),
            vdagent=str(versions["vdagent"]),
            usbipc=str(versions["usbipc"]),
            media_redirect=str(versions["media_redirect"]),
        )
        self.transport.send(self._envelope(
            4004,
            self.host_module,
            emitted_at,
            {
                "test_mode": True,
                "schema": "vmbooster_4004_v1",
                **version_event.as_payload(),
            },
            wire_payload=version_event.encode(),
        ))
        if any(value < 1 for value in (
            self.acks.mac,
            self.acks.network,
            self.acks.os,
            self.acks.csap,
            self.acks.ip_info,
        )):
            self.state = SessionState.DEGRADED
            raise RuntimeError("startup acknowledgement missing")
        self.state = SessionState.HEALTHY

    def heartbeat(self, emitted_at: str, uptime_seconds: int) -> None:
        if self.state is not SessionState.HEALTHY:
            raise RuntimeError(f"cannot heartbeat in state {self.state.name}")
        self.heartbeat_sequence += 1
        payload = {
            "test_mode": True,
            "msgtype": "4002",
            "agentversion": self.identity["agent_version"],
            "vmid": self.identity["test_vmid"],
            "agentstatus": "1",
            "computername": self.environment["computername"],
            "issysprep": str(self.identity.get("issysprep", "0")),
        }
        if self.heartbeat_extensions:
            payload["sequence"] = self.heartbeat_sequence
            payload["uptime_seconds"] = uptime_seconds
        responses = self.transport.exchange(self._envelope(4002, self.host_module, emitted_at, payload))
        before = self.acks.heartbeat
        self._handle_responses(responses)
        if self.acks.heartbeat == before:
            self.missed_heartbeat_acks += 1
            if self.missed_heartbeat_acks >= self.max_missed_heartbeat_acks:
                self.state = SessionState.DEGRADED
            return
        self.missed_heartbeat_acks = 0

    def periodic_status(self, emitted_at: str) -> None:
        if self.state is not SessionState.HEALTHY:
            return
        self.transport.send(self._envelope(8007, self.host_module, emitted_at, {
            "test_mode": True,
            "msgtype": "8007",
            "rdp": str(self.identity.get("rdp_state", "0")),
        }))
        self.transport.send(self._envelope(8059, self.host_module, emitted_at, {
            "test_mode": True,
            "alarmtype": 1,
            "alarmnum": int(self.identity.get("gateway_alarm", 1000028)),
            "gateway": self.environment["gateway"],
            "ip": self.environment["ip"],
            "hostname": self.environment["computername"],
        }))

    def set_lock_state(self, emitted_at: str, locked: bool) -> None:
        """Emit the observed lock-state ID and retain correlation for 9053."""
        if self.state is not SessionState.HEALTHY:
            raise RuntimeError(f"cannot change lock state in state {self.state.name}")
        self.locked = bool(locked)
        self.lock_transition_sequence += 1
        self.transport.send(self._envelope(8060, self.host_module, emitted_at, {
            "test_mode": True,
            "msgtype": "8060",
            "locked": "1" if self.locked else "0",
            "transition_sequence": self.lock_transition_sequence,
        }))
        self.emit_aux_state(emitted_at)

    def activity_state_event(self) -> str:
        state = "locked" if self.locked else "unlocked"
        return (
            f"test_mode=1;session_state={state};"
            f"input_allowed={int(not self.locked)};"
            f"lock_transition_sequence={self.lock_transition_sequence}"
        )

    def activity_input_allowed(self) -> bool:
        """Expose the synthetic session gate used to correlate 9053 input."""
        return not self.locked

    def emit_aux_state(self, emitted_at: str) -> None:
        """Emit only the confirmed one-byte 0/1 shape of 0x8102c4."""
        self.aux_state_sequence += 1
        self.transport.send(self._envelope(0x8102C4, self.aux_host_module, emitted_at, {
            "test_mode": True,
            "raw_state": 1 if self.locked else 0,
        }))

    def emit_ice_client_quit(self, emitted_at: str, msgid: int | None = None) -> IceClientQuit8047:
        """Emit the confirmed IceDisplay 8047 text shape in the Fake Host lab."""
        if msgid is None:
            self.request_sequence += 1
            msgid = self.request_sequence
        raw = build_8047(msgid, str(self.identity["vmuuid"]))
        event = parse_8047(raw)
        self.ice_client_connected = False
        self.last_client_quit = event
        self.transport.send(self._envelope(8047, self.host_module, emitted_at, {
            "test_mode": True,
            "schema": "ice_inside_8047_v1",
            "msgid": event.msgid,
            "vmuuid": event.vmuuid,
            "inside_message": raw.decode("ascii"),
            "ack_expected": False,
        }, wire_payload=event.encode_host_payload()))
        return event

    def handle_ice_inside_message(self, value: bytes | str) -> dict[str, Any]:
        """Parse an 8047 event and update state without inventing a Host ACK."""
        event = parse_8047(value)
        self.ice_client_connected = False
        self.last_client_quit = event
        return {
            "status": "accepted",
            "msgtype": 8047,
            "msgid": event.msgid,
            "vmuuid": event.vmuuid,
            "ack_generated": False,
        }

    def refresh_csap(self, emitted_at: str) -> None:
        self.request_sequence += 1
        token = f"getcsapipport{self.request_sequence:09d}"
        self._handle_responses(self.transport.exchange(self._envelope(8063, self.host_module, emitted_at, {
            "test_mode": True,
            "msgid": token,
        })))

    def request_ip_info(self, emitted_at: str) -> None:
        self.request_sequence += 1
        token = f"getipinfo{self.request_sequence:09d}"
        self._handle_responses(self.transport.exchange(self._envelope(9011, self.host_module, emitted_at, {
            "test_mode": True,
            "msgid": token,
        })))

    def renegotiate(self, emitted_at: str) -> None:
        self.state = SessionState.RENEGOTIATING
        self.start(emitted_at)

    def _handle_responses(self, responses: list[Envelope]) -> None:
        for response in responses:
            if response.int_msgid == 8009 and self.state is SessionState.VM_INFO_PENDING:
                self.session_uuid = str(response.payload["test_session_uuid"])
                self.acks.vm_info += 1
                self.state = SessionState.VM_IDENTIFIED
            elif response.int_msgid == 1400 and self.state is SessionState.BASELINE_PENDING:
                self.mac_uuid = str(response.payload["test_mac_uuid"])
                self.acks.mac += 1
            elif response.int_msgid == 0x8102C1 and self.state is SessionState.BASELINE_PENDING:
                self.acks.network += 1
            elif response.int_msgid == 0x8102C7 and self.state is SessionState.BASELINE_PENDING:
                self.acks.os += 1
            elif response.int_msgid == 8064 and self.state is SessionState.BASELINE_PENDING:
                self.csap_endpoint = (str(response.payload["csapip"]), int(response.payload["csapport"]))
                self.acks.csap += 1
            elif response.int_msgid == 9012 and self.state is SessionState.BASELINE_PENDING:
                self.acks.ip_info += 1
            elif response.int_msgid == 4100 and self.state is SessionState.HEALTHY:
                if self.heartbeat_extensions and int(response.payload["sequence"]) != self.heartbeat_sequence:
                    self.state = SessionState.DEGRADED
                    raise RuntimeError("4100 sequence mismatch")
                self.acks.heartbeat += 1

    def _envelope(
        self,
        msgid: int,
        destination: int,
        emitted_at: str,
        payload: dict[str, Any],
        *,
        wire_payload: bytes | None = None,
    ) -> Envelope:
        return Envelope(msgid, self.vmbooster_module, destination, emitted_at, payload, wire_payload)


class TestHostResponder:
    """Deterministic responder used by Memory and Loopback test providers."""

    def __init__(self, drop_heartbeat_acks: int = 0) -> None:
        self.drop_heartbeat_acks = drop_heartbeat_acks

    def handle(self, request: Envelope) -> list[Envelope]:
        payload = request.payload
        if payload.get("test_mode") is False:
            return []
        if request.int_msgid == 4002 and self.drop_heartbeat_acks > 0:
            self.drop_heartbeat_acks -= 1
            return []
        mapping = {
            8008: (8009, {"test_mode": True, "test_session_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}),
            1300: (1400, {"test_mode": True, "test_mac_uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}),
            0x8102BF: (0x8102C1, {"test_mode": True, "result": 0}),
            0x8102C5: (0x8102C7, {"test_mode": True, "result": 0}),
            4002: (4100, {"test_mode": True, "sequence": payload.get("sequence")}),
            8063: (8064, {
                "test_mode": True,
                "msgid": payload.get("msgid"),
                "csapip": "192.0.2.200",
                "csapport": 19000,
            }),
            9011: (9012, {
                "test_mode": True,
                "msgid": payload.get("msgid"),
                "test_host_uuid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            }),
        }
        if request.int_msgid not in mapping:
            return []
        msgid, response_payload = mapping[request.int_msgid]
        return [Envelope(msgid, request.destination_module, request.source_module, request.emitted_at, response_payload)]
