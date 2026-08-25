from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from message_adapters.scheduler import TelemetryMessageScheduler
from message_adapters.windows import WindowsMessageEncoder
from telemetry.model import TelemetrySnapshot


CONFIG = {
    "agentversion": "V7.25.21SP3pv",
    "versions": {
        "vmbooster": "V7.25.21SP3pv",
        "PVDriver": "3.18.34.723185c6",
        "vdagent": "",
        "usbipc": "",
        "media_redirect": "",
    },
}


def snapshot(second: int) -> TelemetrySnapshot:
    observed = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second)
    row = {
        "name": "VmQoEAgent.exe",
        "pid": 3068,
        "cpu_percent": 0.1,
        "rss_mb": 19.5,
        "handles": 385,
        "threads": 12,
        "disk_io_rate": 4.0,
        "network_io_rate": 8.0,
    }
    return TelemetrySnapshot(
        observed_at=observed.isoformat(),
        provider="windows-validation",
        metrics={
            "local_environment": {
                "VMID": "vm-1",
                "UUID": "uuid-1",
                "HOSTID": "0" * 36,
                "COMPUTERNAME": "WIN TEST",
                "MAC": "00-11-22-33-44-55",
                "IP": "192.0.2.10",
                "CPU": "Test CPU",
                "OS": "Microsoft Windows [版本 10.0]",
                "MEM": "16384M",
                "DISK": "C:80GB",
            },
            "cpu": {"percent": 5.0, "per_core": [4.0, 6.0]},
            "memory": {
                "percent": 33.0,
                "paged_pool_mb": 200.0,
                "nonpaged_pool_mb": 100.0,
            },
            "disk_io": {
                "activity_rate": 2.0,
                "per_disk": [
                    {"name": "C", "size_gb": 80.0, "used_percent": 35.0, "activity_rate": 2.0}
                ],
            },
            "network_io": {
                "tx_bytes_per_second": 100.0,
                "rx_bytes_per_second": 200.0,
            },
            "process_snapshot": {
                "process": [row],
                "process_memory": [row],
                "process_handle": [row],
                "process_diskio": [row],
                "process_netio": [row],
                "keyprocess": "VmQoEAgent.exe",
            },
        },
    )


class MessageAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = TelemetryMessageScheduler(WindowsMessageEncoder(CONFIG), {})

    def test_startup_messages_have_confirmed_fields(self) -> None:
        messages = self.scheduler.messages_for(snapshot(0), 0)
        self.assertEqual([item.int_msgid for item in messages], [4004, 9050, 4002])
        by_id = {item.int_msgid: item.payload for item in messages}
        self.assertEqual(
            list(by_id[4002]),
            ["msgtype", "agentversion", "vmid", "agentstatus", "computername", "issysprep"],
        )
        self.assertEqual(
            list(by_id[4004]),
            ["msgtype", "vmid", "vmbooster", "vmagent", "PVDriver", "vdagent", "usbipc", "media_redirect"],
        )
        self.assertEqual(
            list(by_id[9050]),
            ["source", "uuid", "hostid", "time", "groupid", "createtime", "environment"],
        )
        self.assertNotIn("local_environment", by_id[4002])

    def test_heartbeat_and_five_minute_batches_are_scheduled(self) -> None:
        self.scheduler.messages_for(snapshot(0), 0)
        emitted: list = []
        for second in range(1, 301):
            emitted.extend(self.scheduler.messages_for(snapshot(second), second))

        heartbeats = [item for item in emitted if item.int_msgid == 4002]
        performance = [item for item in emitted if item.int_msgid == 9051]
        processes = [item for item in emitted if item.int_msgid == 9052]
        self.assertEqual(len(heartbeats), 10)
        self.assertEqual(len(performance), 1)
        self.assertEqual(len(performance[0].payload["performance"]), 5)
        self.assertEqual(len(processes), 1)
        self.assertEqual(
            [item["createtime"] for item in performance[0].payload["performance"]],
            [
                "2030-01-01 00:01:00.000",
                "2030-01-01 00:02:00.000",
                "2030-01-01 00:03:00.000",
                "2030-01-01 00:04:00.000",
                "2030-01-01 00:05:00.000",
            ],
        )

    def test_version_message_repeats_only_at_long_interval(self) -> None:
        scheduler = TelemetryMessageScheduler(
            WindowsMessageEncoder(CONFIG),
            {"version_seconds": 7200},
        )
        startup = scheduler.messages_for(snapshot(0), 0)
        before = scheduler.messages_for(snapshot(7199), 7199)
        due = scheduler.messages_for(snapshot(7200), 7200)
        self.assertEqual(sum(item.int_msgid == 4004 for item in startup), 1)
        self.assertFalse(any(item.int_msgid == 4004 for item in before))
        self.assertEqual(sum(item.int_msgid == 4004 for item in due), 1)


if __name__ == "__main__":
    unittest.main()
