from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from message_adapters.mswitch_frame import HEADER_SIZE, MswitchFrameEncoder, MswitchHeader, decode_serial_frame
from message_adapters.scheduler import TelemetryMessageScheduler
from message_adapters.windows import WindowsMessageEncoder
from telemetry.model import TelemetrySnapshot


ROOT = Path(__file__).parents[1]
UUID = "11111111-1111-4111-8111-111111111111"
BASE_CONFIG = {
    "agentversion": "V7.25.21SP3pv",
    "software_profile": str(ROOT / "fixtures" / "observed-software-baseline.json"),
    "environment": {"diskused": "C:23.17GB,D:26.61GB"},
    "versions": {"vmbooster": "V7.25.21SP3pv", "PVDriver": "3.18.34.723185c6"},
}
CLASS_A_CONFIG = {
    **BASE_CONFIG,
    "class_a": {
        "enabled": True,
        "evidence_profile": str(ROOT / "fixtures" / "class-a-observed-baseline.json"),
        "gateway": "192.0.2.1",
    },
}


def snapshot(second: int, *, seed: int = 12345) -> TelemetrySnapshot:
    observed = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second)
    row = {
        "name": "VmQoEAgent", "pid": 3068, "cpu_percent": 0.1,
        "rss_mb": 19.5, "handles": 385, "disk_io_rate": 4.0,
        "network_io_rate": 8.0,
    }
    behavior = ("IDLE", "LIGHT", "NORMAL", "ACTIVE", "SHORT_BURST")[(second // 420) % 5]
    return TelemetrySnapshot(
        observed_at=observed.isoformat(), provider="windows-validation-accelerated",
        metrics={
            "local_environment": {
                "VMID": UUID, "UUID": UUID, "HOSTID": "0" * 36,
                "COMPUTERNAME": "WIN-TEST", "MAC": "00-11-22-33-44-55",
                "IP": "192.0.2.10", "CPU": "Test CPU",
                "OS": "Microsoft Windows [版本 10.0]", "MEM": "16384M",
                "DISK": "C:80GB",
            },
            "cpu": {"percent": 35.0, "per_core": [34.0, 36.0]},
            "memory": {"percent": 38.0, "paged_pool_mb": 200.0, "nonpaged_pool_mb": 150.0},
            "disk_io": {"system_activity": 1.0, "per_disk": []},
            "network_io": {"tx_kb_per_second": 0.2, "rx_kb_per_second": 0.3},
            "process_snapshot": {
                "process": [row], "process_memory": [row], "process_handle": [row],
                "process_diskio": [row], "process_netio": [row],
                "keyprocess": "VmQoEAgent.exe",
            },
        },
        metadata={"run_seed": seed, "behavior_state": behavior},
    )


class ClassAOfflineModelTests(unittest.TestCase):
    def test_startup_order_and_9055_virtual_boot_relation(self) -> None:
        scheduler = TelemetryMessageScheduler(WindowsMessageEncoder(CLASS_A_CONFIG), {})
        messages = scheduler.messages_for(snapshot(0), 0.0)
        self.assertEqual(
            [item.int_msgid for item in messages],
            [9055, 9053, 9050, 9054, 9054, 9054],
        )
        startup_time = datetime.fromisoformat(messages[2].emitted_at)
        boot_time = datetime.fromisoformat(messages[0].emitted_at)
        self.assertIn((startup_time - boot_time).total_seconds(), (1.0, 2.0))
        self.assertEqual(messages[0].payload["uuid"], "0" * 36)
        self.assertEqual(messages[0].payload["logdatas"], [{"log": f"VmStartTime:{messages[0].emitted_at}"}])

    def test_two_hour_lifecycle_and_identity_consistency(self) -> None:
        scheduler = TelemetryMessageScheduler(WindowsMessageEncoder(CLASS_A_CONFIG), {})
        messages = []
        for second in range(7200):
            messages.extend(scheduler.messages_for(snapshot(second), float(second)))
        by_id = {
            msgid: [item for item in messages if item.int_msgid == msgid]
            for msgid in (8007, 8059, 9053, 9055, 9056)
        }
        self.assertEqual(len(by_id[8007]), 23)
        self.assertEqual(len(by_id[8059]), 25)
        self.assertEqual(len(by_id[9055]), 1)
        self.assertEqual(len(by_id[9056]), 23)
        self.assertGreaterEqual(len(by_id[9053]), 2)
        self.assertLessEqual(len(by_id[9053]), 25)
        self.assertTrue(all(item.payload == {"msgtype": "8007", "rdp": "0"} for item in by_id[8007]))
        self.assertEqual(by_id[8059][0].payload, {"alarmtype": "2", "alarmnum": "0"})
        for item in by_id[8059][1:]:
            self.assertEqual(item.payload["gateway"], "192.0.2.1")
            self.assertEqual(item.payload["ip"], "192.0.2.10")
            self.assertEqual(item.payload["hostname"], "WIN-TEST")
        for item in by_id[9056]:
            row = item.payload["datas"][0]["row"]
            self.assertIn("'192.0.2.10'", row)
            self.assertIn("'192.0.2.1:1'", row)

    def test_class_a_does_not_change_frozen_six_messages(self) -> None:
        with_class_a = TelemetryMessageScheduler(WindowsMessageEncoder(CLASS_A_CONFIG), {})
        frozen_only = TelemetryMessageScheduler(WindowsMessageEncoder(BASE_CONFIG), {})
        left, right = [], []
        for second in range(7200):
            current = snapshot(second)
            left.extend(
                item.to_dict() for item in with_class_a.messages_for(current, float(second))
                if item.int_msgid in {4002, 4004, 9050, 9051, 9052, 9054}
            )
            right.extend(
                item.to_dict() for item in frozen_only.messages_for(current, float(second))
            )
        self.assertEqual(left, right)

    def test_wire_encodings_match_observed_plain_and_json_shapes(self) -> None:
        scheduler = TelemetryMessageScheduler(WindowsMessageEncoder(CLASS_A_CONFIG), {})
        messages = []
        for second in range(313):
            messages.extend(scheduler.messages_for(snapshot(second), float(second)))
        encoder = MswitchFrameEncoder(UUID)

        message_8007 = next(item for item in messages if item.int_msgid == 8007)
        raw_8007 = decode_serial_frame(encoder.encode(message_8007))
        self.assertEqual(MswitchHeader.parse(raw_8007).data_len, 23)
        self.assertEqual(raw_8007[HEADER_SIZE:], b"msgtype:'8007',rdp:'0',")

        minimal = next(item for item in messages if item.int_msgid == 8059)
        raw_minimal = decode_serial_frame(encoder.encode(minimal))
        self.assertEqual(raw_minimal[HEADER_SIZE:], b"alarmtype=2;alarmnum=0;")

        populated = [item for item in messages if item.int_msgid == 8059][1]
        raw_populated = decode_serial_frame(encoder.encode(populated))
        self.assertEqual(
            raw_populated[HEADER_SIZE:],
            b"alarmtype=1;alarmnum=1000028;gateway=192.0.2.1;ip=192.0.2.10;hostname=WIN-TEST;",
        )

        for msgid in (9053, 9055, 9056):
            message = next(item for item in messages if item.int_msgid == msgid)
            raw = decode_serial_frame(encoder.encode(message))
            self.assertEqual(json.loads(raw[HEADER_SIZE:]), message.payload)

    def test_same_seed_reproduces_class_a_without_historical_log_text(self) -> None:
        outputs = []
        for _ in range(2):
            scheduler = TelemetryMessageScheduler(WindowsMessageEncoder(CLASS_A_CONFIG), {})
            messages = []
            for second in range(1200):
                messages.extend(scheduler.messages_for(snapshot(second, seed=8877), float(second)))
            outputs.append([
                item.to_dict() for item in messages
                if item.int_msgid in {8007, 8059, 9053, 9055, 9056}
            ])
        self.assertEqual(outputs[0], outputs[1])
        serialized = json.dumps(outputs[0], ensure_ascii=False)
        self.assertNotIn("2026-08", serialized)
        self.assertNotIn("Authentication Successed", serialized)


if __name__ == "__main__":
    unittest.main()
