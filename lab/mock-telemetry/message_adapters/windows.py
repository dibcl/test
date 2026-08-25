from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

if TYPE_CHECKING:
    from telemetry.model import TelemetrySnapshot

from .model import ProtocolMessage


VMBOOSTER_MODULE = 0x80000001
QOE_MODULE = 0x80000011
HOST_MODULE = 0x80000000
QOE_TARGET = 10


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
            "vmid": env["VMID"],
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
            "vmid": env["VMID"],
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
        payload = {
            "source": 4,
            "uuid": env["UUID"],
            "hostid": env["HOSTID"],
            "time": stamp,
            "groupid": "-1",
            "createtime": stamp,
            "environment": {
                "computername": quote_plus(env["COMPUTERNAME"]),
                "cpu": env["CPU"],
                "os": quote_plus(env["OS"]),
                "bit": self.bit,
                "mem": env["MEM"],
                "mac": env["MAC"],
                "ip": env.get("IP", ""),
                "disk": env["DISK"],
                "diskused": self.diskused,
                "version": self.vmbooster,
                "targetversion": self.targetversion,
            },
        }
        return self._message(9050, QOE_MODULE, QOE_TARGET, stamp, payload)

    def performance_sample(self, snapshot: TelemetrySnapshot) -> dict[str, Any]:
        metrics = snapshot.metrics
        env = self._local_environment(snapshot)
        cpu = metrics["cpu"]
        memory = metrics["memory"]
        disk = metrics["disk_io"]
        network = metrics["network_io"]
        processes = metrics["process_snapshot"]
        stamp = self._stamp(snapshot.observed_at)

        handles = sum(int(item.get("handles", 0)) for item in processes.get("process_handle", []))
        tx = network.get("tx_bytes_per_second")
        rx = network.get("rx_bytes_per_second")
        activity = _number(disk.get("activity_rate"))
        disk_columns = [activity] * 9
        per_disk = []
        for item in disk.get("per_disk", []):
            per_disk.append(
                "|".join(
                    [
                        str(item.get("name", "disk")),
                        _number(item.get("size_gb", 0)),
                        _number(item.get("used_percent", 0)),
                        _number(item.get("activity_rate", 0)),
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                    ]
                )
            )
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
                        [env["MAC"], _number(tx), _number(rx), "0", "0", _number(tx), _number(rx)]
                    )
                }
            ],
            "disk": "|".join(disk_columns),
            "perdisk": ";".join(per_disk),
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
            values = [
                _number(item.get("cpu_percent", 0)),
                _number(item.get("rss_mb", 0)),
                str(int(item.get("handles", 0))),
            ]
        elif group == "process_diskio":
            total = _number(item.get("disk_io_rate", 0))
            values = [total, total, "0"]
        else:
            total = _number(item.get("network_io_rate", 0))
            values = [total, total, "0"]
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
