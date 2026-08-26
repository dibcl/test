from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

if TYPE_CHECKING:
    from telemetry.model import TelemetrySnapshot

from .model import ProtocolMessage


VMBOOSTER_MODULE = 0x80000001
QOE_MODULE = 0x80000011
HOST_MODULE = 0x80000000
QOE_TARGET = 10
ZERO_VMID = "0" * 36
SOFTWARE_BATCH_ORDER = ("wow6432node", "native", "kb")
DISK_FIELD_KEYS = (
    "system_activity",
    "primary_used_percent",
    "secondary_used_percent",
    "read_iops",
    "write_iops",
    "read_kb_per_second",
    "write_kb_per_second",
    "latency_ms",
    "queue_length",
)
PER_DISK_FIELD_KEYS = (
    "read_iops",
    "write_iops",
    "read_kb_per_second",
    "write_kb_per_second",
    "read_latency_ms",
    "write_latency_ms",
)


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"message_adapter.{label} must be a string")
    return value


def _number(value: Any) -> str:
    if value is None:
        return "0.0"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("protocol metric value must be numeric")
    return f"{float(value):.3f}".rstrip("0").rstrip(".") or "0"


def _windows_urlencode(value: str) -> str:
    return quote_plus(value, safe="").replace("-", "%2D").replace(".", "%2E")


def _software_batches(config: Mapping[str, Any]) -> tuple[tuple[dict[str, str], ...], ...]:
    profile_path = _text(config.get("software_profile", ""), "software_profile")
    source = Path(profile_path)
    value = json.loads(source.read_text(encoding="utf-8"))
    batches = value.get("batches") if isinstance(value, dict) else None
    if not isinstance(batches, list):
        raise ValueError("message_adapter.software_profile must contain batches")

    indexed: dict[str, tuple[dict[str, str], ...]] = {}
    required = ("name", "type", "publisher", "installtime", "size", "version", "operate")
    for batch in batches:
        if not isinstance(batch, dict) or not isinstance(batch.get("label"), str):
            raise ValueError("software batch must contain a label")
        rows = batch.get("softwares")
        if not isinstance(rows, list):
            raise ValueError("software batch must contain softwares")
        checked: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str) for key in required):
                raise ValueError("software rows must contain the complete string schema")
            checked.append({key: row[key] for key in required})
        indexed[batch["label"]] = tuple(checked)

    missing = [label for label in SOFTWARE_BATCH_ORDER if label not in indexed]
    if missing:
        raise ValueError(f"software profile missing batches: {missing}")
    return tuple(indexed[label] for label in SOFTWARE_BATCH_ORDER)


class WindowsMessageEncoder:
    def __init__(self, config: Mapping[str, Any]) -> None:
        versions = config.get("versions", {})
        environment = config.get("environment", {})
        if not isinstance(versions, Mapping) or not isinstance(environment, Mapping):
            raise ValueError("message_adapter versions and environment must be objects")

        self.agentversion = _text(config.get("agentversion", ""), "agentversion")
        self.agentstatus = _text(config.get("agentstatus", "1"), "agentstatus")
        self.issysprep = _text(config.get("issysprep", "0"), "issysprep")
        self.vmbooster = _text(versions.get("vmbooster", self.agentversion), "versions.vmbooster")
        self.pvdriver = _text(
            versions.get("PVDriver", ""), "versions.PVDriver", allow_empty=True
        )
        self.vdagent = _text(
            versions.get("vdagent", ""), "versions.vdagent", allow_empty=True
        )
        self.usbipc = _text(
            versions.get("usbipc", ""), "versions.usbipc", allow_empty=True
        )
        self.media_redirect = _text(
            versions.get("media_redirect", ""),
            "versions.media_redirect",
            allow_empty=True,
        )
        self.bit = _text(environment.get("bit", "64"), "environment.bit")
        self.diskused = _text(
            environment.get("diskused", ""), "environment.diskused", allow_empty=True
        )
        self.targetversion = _text(
            environment.get("targetversion", ""),
            "environment.targetversion",
            allow_empty=True,
        )
        self.software_batches = _software_batches(config)

    @staticmethod
    def _stamp(observed_at: str) -> str:
        value = datetime.fromisoformat(observed_at)
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    @staticmethod
    def _local_environment(snapshot: TelemetrySnapshot) -> dict[str, str]:
        value = snapshot.metrics.get("local_environment")
        if not isinstance(value, dict):
            raise ValueError("windows protocol adapter requires metrics.local_environment")
        required = ("VMID", "UUID", "HOSTID", "COMPUTERNAME", "MAC", "CPU", "OS", "MEM", "DISK")
        missing = [key for key in required if not isinstance(value.get(key), str)]
        if missing:
            raise ValueError(f"windows protocol adapter missing environment fields: {missing}")
        return value

    @staticmethod
    def _message(
        int_msgid: int,
        source_module: int,
        destination_module: int,
        emitted_at: str,
        payload: dict[str, Any],
    ) -> ProtocolMessage:
        return ProtocolMessage(
            int_msgid,
            source_module,
            destination_module,
            emitted_at,
            payload,
        )

    def heartbeat_4002(self, snapshot: TelemetrySnapshot) -> ProtocolMessage:
        env = self._local_environment(snapshot)
        stamp = self._stamp(snapshot.observed_at)
        payload = {
            "msgtype": "4002",
            "agentversion": self.agentversion,
            "vmid": ZERO_VMID,
            "agentstatus": self.agentstatus,
            "computername": env["COMPUTERNAME"],
            "issysprep": self.issysprep,
        }
        return self._message(4002, VMBOOSTER_MODULE, HOST_MODULE, stamp, payload)

    def version_4004(self, snapshot: TelemetrySnapshot) -> ProtocolMessage:
        env = self._local_environment(snapshot)
        stamp = self._stamp(snapshot.observed_at)
        payload = {
            "msgtype": "4004",
            "vmid": ZERO_VMID,
            "vmbooster": self.vmbooster,
            "vmagent": " ",
            "PVDriver": self.pvdriver,
            "vdagent": self.vdagent,
            "usbipc": self.usbipc,
            "media_redirect": self.media_redirect,
        }
        return self._message(4004, VMBOOSTER_MODULE, HOST_MODULE, stamp, payload)

    def environment_9050(self, snapshot: TelemetrySnapshot) -> ProtocolMessage:
        env = self._local_environment(snapshot)
        stamp = self._stamp(snapshot.observed_at)
        diskused = self.diskused
        disks = snapshot.metrics.get("disk_io", {}).get("per_disk", [])
        if disks and all("used_gb" in item for item in disks):
            diskused = ",".join(
                f"{item.get('name', 'disk')}:{float(item['used_gb']):.2f}GB" for item in disks
            )
        payload = {
            "source": 4,
            "uuid": env["UUID"],
            "hostid": env["HOSTID"],
            "time": stamp,
            "groupid": "-1",
            "createtime": stamp,
            "environment": {
                "computername": _windows_urlencode(env["COMPUTERNAME"]),
                "cpu": env["CPU"],
                "os": _windows_urlencode("\n" + env["OS"]),
                "bit": self.bit,
                "mem": env["MEM"],
                "mac": env["MAC"].lower(),
                "ip": env.get("IP", ""),
                "disk": env["DISK"],
                "diskused": diskused,
                "version": self.vmbooster,
                "targetversion": self.targetversion,
            },
        }
        return self._message(9050, QOE_MODULE, QOE_TARGET, stamp, payload)

    def software_9054(self, snapshot: TelemetrySnapshot) -> list[ProtocolMessage]:
        env = self._local_environment(snapshot)
        stamp = self._stamp(snapshot.observed_at)
        messages: list[ProtocolMessage] = []
        for batch in self.software_batches:
            softwares = []
            for item in batch:
                row = dict(item)
                row["name"] = _windows_urlencode(row["name"])
                row["publisher"] = _windows_urlencode(row["publisher"])
                softwares.append(row)
            payload = {
                "source": 4,
                "uuid": env["UUID"],
                "hostid": env["HOSTID"],
                "createtime": stamp,
                "mothod": "1",
                "softwares": softwares,
            }
            messages.append(self._message(9054, QOE_MODULE, QOE_TARGET, stamp, payload))
        return messages

    def performance_sample(self, snapshot: TelemetrySnapshot) -> dict[str, Any]:
        metrics = snapshot.metrics
        env = self._local_environment(snapshot)
        cpu = metrics["cpu"]
        memory = metrics["memory"]
        disk = metrics["disk_io"]
        network = metrics["network_io"]
        processes = metrics["process_snapshot"]
        stamp = self._stamp(snapshot.observed_at)

        handles = int(processes.get(
            "system_handles",
            sum(int(item.get("handles", 0)) for item in processes.get("process_handle", [])),
        ))
        tx = network.get("tx_kb_per_second")
        rx = network.get("rx_kb_per_second")
        if tx is None and network.get("tx_bytes_per_second") is not None:
            tx = float(network["tx_bytes_per_second"]) / 1024.0
        if rx is None and network.get("rx_bytes_per_second") is not None:
            rx = float(network["rx_bytes_per_second"]) / 1024.0
        tx_total = network.get("tx_interval_kb", (float(tx) * 60.0 if tx is not None else 0))
        rx_total = network.get("rx_interval_kb", (float(rx) * 60.0 if rx is not None else 0))
        disks = list(disk.get("per_disk", []))
        read_iops = sum(float(item.get("read_iops", 0)) for item in disks)
        write_iops = sum(float(item.get("write_iops", 0)) for item in disks)
        read_rate = sum(float(item.get("read_kb_per_second", 0)) for item in disks)
        write_rate = sum(float(item.get("write_kb_per_second", 0)) for item in disks)
        latency_values = [
            float(item.get(key, 0)) for item in disks
            for key in ("read_latency_ms", "write_latency_ms")
        ]
        disk_values = {
            "system_activity": disk.get("system_activity", disk.get("activity_rate", 0)),
            "primary_used_percent": disks[0].get("used_percent", 0) if disks else 0,
            "secondary_used_percent": disks[1].get("used_percent", 0) if len(disks) > 1 else 0,
            "read_iops": read_iops, "write_iops": write_iops,
            "read_kb_per_second": read_rate, "write_kb_per_second": write_rate,
            "latency_ms": sum(latency_values) / len(latency_values) if latency_values else 0,
            "queue_length": sum(float(item.get("queue_length", 0)) for item in disks),
        }
        disk_columns = [_number(disk_values.get(key, 0)) for key in DISK_FIELD_KEYS]
        per_disk = []
        for item in disk.get("per_disk", []):
            per_disk.append("|".join([
                str(item.get("name", "disk")),
                _number(item.get("size_gb", 0)),
                _number(item.get("used_gb", 0)),
                _number(item.get("used_percent", 0)),
                *[_number(item.get(key, 0)) for key in PER_DISK_FIELD_KEYS],
            ]))
        return {
            "createtime": stamp,
            "cpu": float(cpu["percent"]),
            "cpus": [
                {"data": f"CPU{index}|{_number(value)}"}
                for index, value in enumerate(cpu.get("per_core", []))
            ],
            "handle": handles,
            "mem": {
                "used": float(memory["percent"]),
                "pagedpool": float(memory["paged_pool_mb"]),
                "nonpagedpool": float(memory["nonpaged_pool_mb"]),
            },
            "network": [
                {
                    "data": "|".join(
                        [env["MAC"], _number(tx), _number(rx), "0", "0", _number(tx_total), _number(rx_total)]
                    )
                }
            ],
            "disk": "|".join(disk_columns),
            "perdisk": ",".join(per_disk),
        }

    def performance_9051(
        self,
        snapshot: TelemetrySnapshot,
        samples: list[dict[str, Any]],
    ) -> ProtocolMessage:
        env = self._local_environment(snapshot)
        stamp = self._stamp(snapshot.observed_at)
        payload = {
            "source": 4,
            "uuid": env["UUID"],
            "hostid": env["HOSTID"],
            "time": stamp,
            "groupid": "-1",
            "performance": samples,
        }
        return self._message(9051, QOE_MODULE, QOE_TARGET, stamp, payload)

    @staticmethod
    def _process_row(group: str, item: Mapping[str, Any]) -> dict[str, str]:
        base = [
            str(item["name"]),
            str(item["pid"]),
        ]
        if group in {"process", "process_memory", "process_handle"}:
            memory = round(float(item.get("rss_mb", 0)) * 1024)
            values = [
                _number(item.get("cpu_percent", 0)),
                _number(memory),
                str(int(item.get("handles", 0))),
            ]
        elif group == "process_diskio":
            total = float(item.get("disk_io_rate", 0))
            read = float(item.get("disk_read_rate", total * 0.65))
            write = float(item.get("disk_write_rate", max(0.0, total - read)))
            values = [_number(total), _number(read), _number(write)]
        else:
            total = float(item.get("network_io_rate", 0))
            tx = float(item.get("network_tx_rate", total * 0.45))
            rx = float(item.get("network_rx_rate", max(0.0, total - tx)))
            values = [_number(total), _number(tx), _number(rx)]
        return {"data": "|".join([*base, *values])}

    def process_9052(self, snapshot: TelemetrySnapshot) -> ProtocolMessage:
        env = self._local_environment(snapshot)
        stamp = self._stamp(snapshot.observed_at)
        source = snapshot.metrics["process_snapshot"]
        groups = (
            "process",
            "process_memory",
            "process_handle",
            "process_diskio",
            "process_netio",
        )
        payload: dict[str, Any] = {
            "source": 4,
            "uuid": env["UUID"],
            "hostid": env["HOSTID"],
            "groupid": "-1",
            "time": stamp,
            "createtime": stamp,
        }
        for group in groups:
            payload[group] = [self._process_row(group, item) for item in source.get(group, [])]
        payload["keyprocess"] = str(source.get("keyprocess", ""))
        return self._message(9052, QOE_MODULE, QOE_TARGET, stamp, payload)
