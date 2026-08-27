from __future__ import annotations

import json
import random
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

if TYPE_CHECKING:
    from telemetry.model import TelemetrySnapshot


CLASS_A_IDS = (8007, 8059, 9053, 9055, 9056)
VMBOOSTER_MODULE = 0x80000001
QOE_MODULE = 0x80000011
HOST_MODULE = 0x80000000
QOE_TARGET = 10
ZERO_ID = "0" * 36
VM_ICE_COLUMNS = (
    "time,createtime,source,source_id,source_ip,internet_status,"
    "gateway_status,dns_status,ip_status"
)


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _encoded_log(value: str) -> str:
    return quote_plus(value, safe="").replace("-", "%2D").replace(".", "%2E")


class ClassAOfflineModel:
    """Evidence-bounded Class A state; it never performs Host I/O.

    Random streams are derived lazily from the provider run seed and remain
    separate from workload/timestamp RNGs in the sealed accelerated provider.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        enabled = config.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("message_adapter.class_a.enabled must be a boolean")
        self.enabled = enabled
        self.gateway = str(config.get("gateway", ""))
        baseline = config.get("evidence_profile")
        if self.enabled:
            if not isinstance(baseline, str) or not baseline:
                raise ValueError("enabled class_a requires evidence_profile")
            self.evidence = json.loads(Path(baseline).read_text(encoding="utf-8"))
            if not self.gateway:
                raise ValueError("enabled class_a requires the observed gateway identity")
        else:
            self.evidence = {}

        self._seed: int | None = None
        self.lifecycle_random: random.Random | None = None
        self.log_random: random.Random | None = None
        self.offset_random: random.Random | None = None
        self.log_queue: deque[dict[str, Any]] = deque(maxlen=128)
        self.last_behavior_state: str | None = None
        self.last_minute_bucket = -1
        self.log_batch_active = False
        self.connectivity = {
            "internet_status": "1",
            "dns_status": "1",
            "ip_status": "1",
        }

    def _ensure_seed(self, snapshot: TelemetrySnapshot) -> None:
        if not self.enabled or self._seed is not None:
            return
        raw = snapshot.metadata.get("run_seed", 0)
        seed = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else 0
        self._seed = seed
        self.lifecycle_random = random.Random(seed ^ 0xA54FF53A5F1D36F1)
        self.log_random = random.Random(seed ^ 0x510E527FADE682D1)
        self.offset_random = random.Random(seed ^ 0x9B05688C2B3E6C1F)
        self.log_batch_active = self.lifecycle_random.random() < 0.54

    @staticmethod
    def _env(snapshot: TelemetrySnapshot) -> dict[str, str]:
        value = snapshot.metrics.get("local_environment")
        if not isinstance(value, dict):
            raise ValueError("Class A requires metrics.local_environment")
        required = ("UUID", "HOSTID", "COMPUTERNAME", "IP")
        if any(not isinstance(value.get(key), str) for key in required):
            raise ValueError("Class A local identity is incomplete")
        return value

    def observe(self, snapshot: TelemetrySnapshot, elapsed: float) -> None:
        if not self.enabled:
            return
        self._ensure_seed(snapshot)
        assert self.log_random is not None
        behavior = str(snapshot.metadata.get("behavior_state", "UNKNOWN"))
        observed = datetime.fromisoformat(snapshot.observed_at)
        if self.last_behavior_state is None:
            self._append_log_event(observed, "208|3|6", behavior, "startup")
        elif behavior != self.last_behavior_state:
            self._append_log_event(
                observed,
                "208|3|6",
                behavior,
                f"state-from-{self.last_behavior_state}",
            )
            if behavior == "SHORT_BURST":
                for index in range(self.log_random.randint(1, 4)):
                    self._append_log_event(
                        observed + timedelta(milliseconds=7 * (index + 1)),
                        "5|16|1",
                        behavior,
                        f"burst-sample-{index + 1}",
                    )
        self.last_behavior_state = behavior

        minute = int(elapsed // 60.0)
        if minute > self.last_minute_bucket:
            self.last_minute_bucket = minute
            if self.log_random.random() < 0.10:
                metrics = snapshot.metrics
                cpu = float(metrics.get("cpu", {}).get("percent", 0.0))
                memory = float(metrics.get("memory", {}).get("percent", 0.0))
                self._append_log_event(
                    observed,
                    "5|3|15",
                    behavior,
                    f"cpu={cpu:.1f},memory={memory:.1f}",
                )

    def _append_log_event(
        self, observed: datetime, category: str, state: str, detail: str
    ) -> None:
        assert self.log_random is not None
        fields = category.split("|")
        text = (
            f"{_stamp(observed)}|0|{fields[0]}|{fields[1]}|{fields[2]}|||"
            f"|synthetic-runtime state={state} detail={detail}"
        )
        encoded = self.log_random.random() < 1530 / 1541
        self.log_queue.append({"log": _encoded_log(text) if encoded else text})

    def startup_offset(self, snapshot: TelemetrySnapshot) -> float:
        self._ensure_seed(snapshot)
        assert self.offset_random is not None
        return 2.0 if self.offset_random.random() < (3 / 9) else 1.0

    def message_9055(self, snapshot: TelemetrySnapshot):
        from .model import ProtocolMessage

        self._ensure_seed(snapshot)
        boot_time = datetime.fromisoformat(snapshot.observed_at) - timedelta(
            seconds=self.startup_offset(snapshot)
        )
        stamp = _stamp(boot_time)
        return ProtocolMessage(
            9055, QOE_MODULE, QOE_TARGET, stamp,
            {
                "source": 4,
                "uuid": ZERO_ID,
                "hostid": ZERO_ID,
                "time": stamp,
                "logdatas": [{"log": f"VmStartTime:{stamp}"}],
            },
        )

    def message_8007(self, snapshot: TelemetrySnapshot):
        from .model import ProtocolMessage

        stamp = _stamp(datetime.fromisoformat(snapshot.observed_at))
        return ProtocolMessage(
            8007, VMBOOSTER_MODULE, HOST_MODULE, stamp,
            {"msgtype": "8007", "rdp": "0"},
        )

    def message_8059(self, snapshot: TelemetrySnapshot, *, startup: bool):
        from .model import ProtocolMessage

        stamp = _stamp(datetime.fromisoformat(snapshot.observed_at))
        if startup:
            payload = {"alarmtype": "2", "alarmnum": "0"}
        else:
            env = self._env(snapshot)
            payload = {
                "alarmtype": "1",
                "alarmnum": "1000028",
                "gateway": self.gateway,
                "ip": env["IP"],
                "hostname": env["COMPUTERNAME"],
            }
        return ProtocolMessage(8059, VMBOOSTER_MODULE, HOST_MODULE, stamp, payload)

    def should_emit_log_batch(self, snapshot: TelemetrySnapshot) -> bool:
        self._ensure_seed(snapshot)
        assert self.lifecycle_random is not None
        if self.log_batch_active:
            self.log_batch_active = self.lifecycle_random.random() < 0.82
        else:
            self.log_batch_active = self.lifecycle_random.random() < 0.22
        return self.log_batch_active

    def message_9053(self, snapshot: TelemetrySnapshot, *, startup: bool = False):
        from .model import ProtocolMessage

        self._ensure_seed(snapshot)
        env = self._env(snapshot)
        stamp = _stamp(datetime.fromisoformat(snapshot.observed_at))
        if startup and not self.log_queue:
            self._append_log_event(
                datetime.fromisoformat(snapshot.observed_at), "208|3|6",
                str(snapshot.metadata.get("behavior_state", "UNKNOWN")), "startup",
            )
        elif not self.log_queue and self.log_random is not None:
            if self.log_random.random() >= (2 / 592):
                self._append_log_event(
                    datetime.fromisoformat(snapshot.observed_at),
                    "208|3|6",
                    str(snapshot.metadata.get("behavior_state", "UNKNOWN")),
                    "periodic-state",
                )
        count = min(45, len(self.log_queue))
        logs = [self.log_queue.popleft() for _ in range(count)]
        return ProtocolMessage(
            9053, QOE_MODULE, QOE_TARGET, stamp,
            {
                "source": 4,
                "uuid": env["UUID"],
                "hostid": env["HOSTID"],
                "time": stamp,
                "logdatas": logs,
            },
        )

    def message_9056(self, snapshot: TelemetrySnapshot):
        from .model import ProtocolMessage

        self._ensure_seed(snapshot)
        assert self.offset_random is not None
        env = self._env(snapshot)
        emitted = datetime.fromisoformat(snapshot.observed_at)
        row_time = emitted - timedelta(
            seconds=max(3.06, min(4.83, self.offset_random.gauss(3.811, 0.429)))
        )
        stamp = _stamp(emitted)
        row_stamp = _stamp(row_time)
        gateway_status = f"{self.gateway}:1"
        state = self.connectivity
        row = (
            f"'{row_stamp}','{row_stamp}',4,'{env['UUID']}','{env['IP']}',"
            f"'{state['internet_status']}','{gateway_status}',"
            f"'{state['dns_status']}','{state['ip_status']}'"
        )
        return ProtocolMessage(
            9056, QOE_MODULE, QOE_TARGET, stamp,
            {
                "tablename": "vm_ice",
                "columnname": VM_ICE_COLUMNS,
                "datas": [{"row": row}],
            },
        )
