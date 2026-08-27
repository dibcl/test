"""Static fault-injection telemetry stub with strictly local test transports.

The data source is a frozen JSON profile. This module does not inspect the host
OS, registry, packages, processes, network adapters, or user activity. Socket
transports are intentionally restricted to loopback/local test endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import json
import random
import socket
import struct
from typing import Any, Protocol

from dynamic_metrics import DynamicMetricsEngine
from static_payloads import StaticPayloadBuilder


VMBOOSTER_MODULE = 0x80000001
QOE_MODULE = 0x80000011
HOST_MODULE = 0x80000000
QOE_TARGET = 10


class MockTelemetryError(ValueError):
    pass


@dataclass(frozen=True)
class Envelope:
    int_msgid: int
    source_module: int
    destination_module: int
    emitted_at: str
    payload: dict[str, Any]
    wire_payload: bytes | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "int_msgid": self.int_msgid,
            "source_module": self.source_module,
            "destination_module": self.destination_module,
            "emitted_at": self.emitted_at,
            "payload": self.payload,
        }


class Transport(Protocol):
    def send(self, envelope: Envelope) -> None: ...
    def exchange(self, envelope: Envelope) -> list[Envelope]: ...
    def close(self) -> None: ...


@dataclass
class InMemoryTransport:
    messages: list[Envelope] = field(default_factory=list)
    responder: Any | None = None

    def send(self, envelope: Envelope) -> None:
        self.messages.append(envelope)

    def exchange(self, envelope: Envelope) -> list[Envelope]:
        self.send(envelope)
        if self.responder is None:
            return []
        responses = self.responder.handle(envelope)
        self.messages.extend(responses)
        return responses

    def close(self) -> None:
        return None


class LoopbackJsonTcpTransport:
    """Length-prefixed JSON transport restricted to loopback test servers."""

    def __init__(self, host: str, port: int, timeout: float = 3.0, max_bytes: int = 10 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        if not addresses or any(not ip_address(value).is_loopback for value in addresses):
            raise MockTelemetryError("loopback_tcp only permits loopback destinations")
        if not 1 <= port <= 65535:
            raise MockTelemetryError("port must be between 1 and 65535")
        self._socket = socket.create_connection((host, port), timeout=timeout)

    def send(self, envelope: Envelope) -> None:
        body = json.dumps(envelope.as_dict(), ensure_ascii=True, separators=(",", ":")).encode()
        if len(body) > self.max_bytes:
            raise MockTelemetryError(f"test envelope exceeds max allowed size of {self.max_bytes} bytes")
        self._socket.sendall(struct.pack("!I", len(body)) + body)

    def exchange(self, envelope: Envelope) -> list[Envelope]:
        self.send(envelope)
        header = self._recv_exact(4)
        size = struct.unpack("!I", header)[0]
        if size > self.max_bytes:
            raise MockTelemetryError(f"test response exceeds max allowed size of {self.max_bytes} bytes")
        value = json.loads(self._recv_exact(size))
        responses = value if isinstance(value, list) else [value]
        return [Envelope(
            int(item["int_msgid"]),
            int(item["source_module"]),
            int(item["destination_module"]),
            str(item["emitted_at"]),
            dict(item["payload"]),
        ) for item in responses]

    def _recv_exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self._socket.recv(size - len(result))
            if not chunk:
                raise MockTelemetryError("test server closed during response")
            result.extend(chunk)
        return bytes(result)

    def close(self) -> None:
        self._socket.close()


@dataclass(frozen=True)
class FrozenProfile:
    identity: dict[str, Any]
    environment: dict[str, Any]
    software_batches: tuple[tuple[dict[str, Any], ...], ...]
    performance_samples: tuple[dict[str, Any], ...]
    process_snapshot: dict[str, Any]
    activity_events: tuple[str, ...]
    connectivity_rows: tuple[str, ...]
    ice_traces: tuple[dict[str, Any], ...]
    dynamics: dict[str, Any] | None
    protocol_options: dict[str, Any]

    @classmethod
    def load(cls, path: str) -> "FrozenProfile":
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FrozenProfile":
        required = {
            "identity", "environment", "software_batches", "performance_samples",
            "process_snapshot", "activity_events", "connectivity_rows", "ice_traces",
        }
        missing = required - value.keys()
        if missing:
            raise MockTelemetryError(f"profile missing keys: {sorted(missing)}")
        batches = tuple(tuple(dict(item) for item in batch) for batch in value["software_batches"])
        if value["identity"].get("test_mode") is not True:
            raise MockTelemetryError("profile identity must contain test_mode=true")
        return cls(
            identity=dict(value["identity"]),
            environment=dict(value["environment"]),
            software_batches=batches,
            performance_samples=tuple(dict(item) for item in value["performance_samples"]),
            process_snapshot=dict(value["process_snapshot"]),
            activity_events=tuple(str(item) for item in value["activity_events"]),
            connectivity_rows=tuple(str(item) for item in value["connectivity_rows"]),
            ice_traces=tuple(dict(item) for item in value["ice_traces"]),
            dynamics=dict(value["dynamics"]) if value.get("dynamics") else None,
            protocol_options=dict(value.get("protocol_options", {})),
        )


@dataclass(frozen=True)
class FaultPlan:
    drop_ids: frozenset[int] = frozenset()
    duplicate_ids: frozenset[int] = frozenset()
    stale_timestamp_ids: frozenset[int] = frozenset()

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "FaultPlan":
        value = value or {}
        return cls(
            frozenset(int(item) for item in value.get("drop_ids", [])),
            frozenset(int(item) for item in value.get("duplicate_ids", [])),
            frozenset(int(item) for item in value.get("stale_timestamp_ids", [])),
        )


class MockTelemetryAgent:
    def __init__(
        self,
        profile: FrozenProfile,
        transport: Transport,
        *,
        start_time: datetime | None = None,
        faults: FaultPlan | None = None,
        control_session: Any | None = None,
    ) -> None:
        self.profile = profile
        self.transport = transport
        self.now = start_time or datetime(2030, 1, 1, tzinfo=timezone.utc)
        if self.now.tzinfo is None:
            raise MockTelemetryError("start_time must be timezone-aware")
        self.faults = faults or FaultPlan()
        self.control_session = control_session
        module_ids = dict(profile.identity.get("module_ids", {}))
        self.vmbooster_module = int(module_ids.get("vmbooster", VMBOOSTER_MODULE))
        self.qoe_module = int(module_ids.get("qoe", QOE_MODULE))
        self.host_module = int(module_ids.get("host", HOST_MODULE))
        self.qoe_target = int(module_ids.get("qoe_target", QOE_TARGET))
        self.boot_time = self.now
        self.sent_counts: dict[int, int] = {}
        self.dynamic_engine = DynamicMetricsEngine(profile.dynamics) if profile.dynamics else None
        self.static_builder = StaticPayloadBuilder.from_profile(profile)
        seed = int((profile.dynamics or {}).get("seed", 0)) ^ 0x5A17E2
        self.timer_rng = random.Random(seed)
        self.timer_options = dict(profile.protocol_options.get("timers", {}))
        self._next_heartbeat = self.now
        self._next_qoe = self.now + timedelta(seconds=self._timer_delay("qoe", 298.0, 303.0))

    def start(self) -> None:
        if self.control_session is not None:
            self.control_session.start(self._stamp())
        self.emit_startup()

    def emit_startup(self) -> None:
        stamp = self._stamp()
        self._emit(9055, self.qoe_module, self.qoe_target, self.static_builder.startup_time_payload(stamp))
        self._emit(9050, self.qoe_module, self.qoe_target, self.static_builder.environment_payload(stamp))
        for payload in self.static_builder.software_payloads(stamp):
            self._emit(9054, self.qoe_module, self.qoe_target, payload)
        for trace in self.profile.ice_traces:
            self._emit(9060, self.qoe_module, self.qoe_target, dict(trace))

    def run_for(self, seconds: int) -> None:
        if seconds < 0:
            raise MockTelemetryError("seconds cannot be negative")
        end = self.now + timedelta(seconds=seconds)
        while min(self._next_heartbeat, self._next_qoe) <= end:
            if self._next_heartbeat <= self._next_qoe:
                self.now = self._next_heartbeat
                self._emit_heartbeat()
                self._next_heartbeat += timedelta(seconds=self._timer_delay("heartbeat", 28.0, 31.0))
            else:
                self.now = self._next_qoe
                self._emit_qoe_window()
                self._next_qoe += timedelta(seconds=self._timer_delay("qoe", 298.0, 303.0))
        self.now = end

    def _emit_heartbeat(self) -> None:
        if self.control_session is not None:
            if 4002 not in self.faults.drop_ids:
                copies = 2 if 4002 in self.faults.duplicate_ids else 1
                for _ in range(copies):
                    self.control_session.heartbeat(self._stamp(), int((self.now - self.boot_time).total_seconds()))
                    self.sent_counts[4002] = self.sent_counts.get(4002, 0) + 1
            return
        identity = self.profile.identity
        self._emit(4002, self.vmbooster_module, self.host_module, {
            "msgtype": "4002",
            "agentversion": identity["agent_version"],
            "vmid": identity["test_vmid"],
            "agentstatus": "1",
            "computername": self.profile.environment["computername"],
            "issysprep": "0",
        })

    def _emit_qoe_window(self) -> None:
        identity = self.profile.identity
        common = {
            "source": 4,
            "uuid": identity["test_uuid"],
            "hostid": identity["test_hostid"],
            "groupid": "-1",
            "time": self._stamp(),
        }
        if self.control_session is not None:
            self.control_session.periodic_status(self._stamp())
        performance = [dict(item) for item in self.profile.performance_samples]
        process_snapshot = self.static_builder.process_snapshot()
        activity_events = self.static_builder.activity_events()
        if self.dynamic_engine is not None:
            dynamic_samples = [self.dynamic_engine.sample(self.now - timedelta(minutes=4 - offset)) for offset in range(5)]
            templates = self.profile.performance_samples
            if not templates:
                raise MockTelemetryError("dynamic metrics require at least one performance template")
            performance = []
            for offset, dynamic in enumerate(dynamic_samples):
                sample = json.loads(json.dumps(templates[offset % len(templates)]))
                sample["createtime"] = dynamic.timestamp
                sample["cpu"] = dynamic.cpu
                if isinstance(sample.get("mem"), dict):
                    sample["mem"]["used"] = dynamic.memory
                for network in sample.get("network", []):
                    if not isinstance(network, dict) or not isinstance(network.get("data"), str):
                        continue
                    columns = network["data"].split("|")
                    if len(columns) >= 3:
                        columns[1] = f"{dynamic.network_io:.3f}"
                        columns[2] = f"{dynamic.network_io * 0.6:.3f}"
                        network["data"] = "|".join(columns)
                if isinstance(sample.get("disk"), str):
                    disk_columns = sample["disk"].split("|")
                    sample["disk"] = "|".join(f"{dynamic.disk_io:.3f}" for _ in disk_columns)
                performance.append(sample)
        if self.control_session is not None and hasattr(self.control_session, "activity_state_event"):
            activity_events.append(self.control_session.activity_state_event())
        self._emit(9051, self.qoe_module, self.qoe_target, {**common, "performance": performance})
        self._emit(9052, self.qoe_module, self.qoe_target, {**common, "createtime": self._stamp(), **process_snapshot})
        self._emit(9053, self.qoe_module, self.qoe_target, {**common, "logdatas": [{"log": item} for item in activity_events]})
        self._emit(9056, self.qoe_module, self.qoe_target, {
            "tablename": "vm_ice",
            "columnname": "time,createtime,source,source_id,source_ip,internet_status,gateway_status,dns_status,ip_status",
            "datas": [{"row": item} for item in self.profile.connectivity_rows],
        })

    def _emit(self, msgid: int, source: int, destination: int, payload: dict[str, Any]) -> None:
        if msgid in self.faults.drop_ids:
            return
        emitted_at = self._stamp()
        if msgid in self.faults.stale_timestamp_ids:
            emitted_at = (self.now - timedelta(days=1)).isoformat()
        envelope = Envelope(msgid, source, destination, emitted_at, payload)
        copies = 2 if msgid in self.faults.duplicate_ids else 1
        for _ in range(copies):
            self.transport.send(envelope)
            self.sent_counts[msgid] = self.sent_counts.get(msgid, 0) + 1

    def _stamp(self) -> str:
        return self.now.isoformat(timespec="milliseconds")

    def _timer_delay(self, name: str, default_min: float, default_max: float) -> float:
        options = dict(self.timer_options.get(name, {}))
        minimum = float(options.get("min_seconds", default_min))
        maximum = float(options.get("max_seconds", default_max))
        if minimum <= 0 or maximum < minimum:
            raise MockTelemetryError(f"invalid {name} timer bounds")
        distribution = options.get("distribution", "uniform")
        if distribution == "uniform":
            return self.timer_rng.uniform(minimum, maximum)
        if distribution == "gaussian":
            mean = float(options.get("mean_seconds", (minimum + maximum) / 2))
            sigma = float(options.get("sigma_seconds", max(0.001, (maximum - minimum) / 6)))
            return max(minimum, min(maximum, self.timer_rng.gauss(mean, sigma)))
        raise MockTelemetryError(f"unsupported {name} timer distribution: {distribution}")

    def close(self) -> None:
        self.transport.close()


def build_transport(config: dict[str, Any]) -> Transport:
    kind = config.get("type", "memory")
    if kind == "memory":
        responder = None
        if config.get("auto_reply", False):
            from mock_guest_session import TestHostResponder
            responder = TestHostResponder()
        return InMemoryTransport(responder=responder)
    if kind == "loopback_tcp":
        if "allow_external" in config:
            raise MockTelemetryError("allow_external is not supported; prototype transports are local-only")
        return LoopbackJsonTcpTransport(
            str(config.get("host", "127.0.0.1")),
            int(config["port"]),
            float(config.get("timeout", 3.0)),
        )
    if kind == "loopback_mswitch_tcp":
        from mswitch_frame_transport import LoopbackMswitchTcpTransport
        return LoopbackMswitchTcpTransport(
            str(config.get("host", "127.0.0.1")),
            int(config["port"]),
            str(config["test_uuid"]),
            timeout=float(config.get("timeout", 3.0)),
            dst_type=int(config.get("dst_type", 0)),
            dst_type_by_module={int(key, 0): int(value) for key, value in config.get("dst_type_by_module", {}).items()},
            msgtype_by_id={int(key, 0): int(value) for key, value in config.get("msgtype_by_id", {}).items()},
            test_mode=config.get("test_mode") is True,
        )
    if kind == "loopback_mswitch_unix":
        from mswitch_frame_transport import LoopbackMswitchUnixTransport
        return LoopbackMswitchUnixTransport(
            str(config["path"]),
            str(config["test_uuid"]),
            timeout=float(config.get("timeout", 3.0)),
            test_mode=config.get("test_mode") is True,
        )
    if kind == "local_test_mswitch_named_pipe":
        from mswitch_frame_transport import LocalTestNamedPipeMswitchTransport
        return LocalTestNamedPipeMswitchTransport(
            str(config["pipe_name"]),
            str(config["test_uuid"]),
            test_mode=config.get("test_mode") is True,
        )
    raise MockTelemetryError(f"unsupported transport provider: {kind}")
