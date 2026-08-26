from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from telemetry_log_compare import Event, compare, process_trend
from run_accelerated_validation import _cpu_memory_checks, _real_source_provenance, _sha256


def protocol_row(second: int, msgid: int, payload: dict) -> dict:
    minute, remainder = divmod(second, 60)
    return {
        "int_msgid": msgid,
        "source_module": 0x80000011,
        "destination_module": 10,
        "emitted_at": f"2030-01-01 00:{minute:02d}:{remainder:02d}.000",
        "payload": payload,
    }


class TelemetryLogCompareTests(unittest.TestCase):
    def test_process_trend_preserves_concurrent_same_name_pid_identities(self) -> None:
        events = [
            Event(
                timestamp=datetime.fromisoformat("2030-01-01T00:00:00+00:00"),
                msgid=9052,
                direction="guest_to_host",
                payload={"process": [
                    {"data": "foo|100|1.0|100|5"},
                    {"data": "foo|200|0.9|90|4"},
                    {"data": "bar|300|0.8|80|3"},
                ]},
            ),
            Event(
                timestamp=datetime.fromisoformat("2030-01-01T00:05:00+00:00"),
                msgid=9052,
                direction="guest_to_host",
                payload={"process": [
                    {"data": "foo|100|1.0|100|5"},
                    {"data": "foo|250|0.9|90|4"},
                    {"data": "bar|300|0.8|80|3"},
                ]},
            ),
        ]

        result = process_trend(events)

        self.assertEqual(result["unique_process_name_count"], 2)
        self.assertEqual(result["unique_pid_count"], 4)
        self.assertEqual(result["pid_transition_count"], 4)
        self.assertEqual(result["pid_change_count"], 2)
        self.assertLessEqual(result["pid_change_share"], 1.0)

    def test_real_source_provenance_binds_bytes_and_package_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vmswitch.log"
            path.write_bytes(b"official-real-bytes\n")
            result = _real_source_provenance(
                [path], [Path("02_real_fresh_2h/vmswitch_fresh_2h.log")]
            )
        self.assertEqual(result[0]["original_source_path"], str(path.resolve()))
        self.assertEqual(
            result[0]["package_relative_path"],
            "02_real_fresh_2h/vmswitch_fresh_2h.log",
        )
        self.assertEqual(len(result[0]["sha256"]), 64)

    def test_validation_provenance_hashes_exact_file_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.bin"
            path.write_bytes(b"messages\nframes\x00")
            self.assertEqual(
                _sha256(path),
                "253a232fc51f3c9b4500648474d3978b2be7b7da65c390de1f0517b035e43ef9",
            )

    def test_accelerated_validator_detects_exact_per_core_boundary(self) -> None:
        result = _cpu_memory_checks(
            [40.0, 41.0],
            [35.0, 35.1],
            [58.49, 58.49, 42.0],
        )
        self.assertTrue(result["fixed_boundary_values"]["detected"])
        self.assertEqual(result["fixed_boundary_values"]["per_core_cpu"]["58.49"], 2)

    def test_comparison_emits_all_six_statistical_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "vmswitch.log"
            runtime = root / "runtime.jsonl"
            real.write_text("\n".join([
                '2030-01-01 00:00:00 F[x] L[INFO]WriteSerialPort succ, ret=641, dst_type=1, int_msgid=4002, dst_mod=-2147483648, data_len=512,msg={"msgtype":"4002","agentstatus":"1"}',
                '2030-01-01 00:00:30 F[x] L[INFO]WriteSerialPort succ, ret=641, dst_type=1, int_msgid=4002, dst_mod=-2147483648, data_len=512,msg={"msgtype":"4002","agentstatus":"1"}',
                '2030-01-01 00:05:00 F[x] L[INFO]WriteSerialPort succ, ret=700, dst_type=1, int_msgid=9051, dst_mod=10, data_len=571,msg={"source":4,"performance":[{"cpu":2.0,"mem":{"used":20.0},"network":[{"data":"AA|1|2|0|0|3|4"}],"disk":"5|1|2"}]}',
                '2030-01-01 00:05:07 F[x] L[INFO]WriteSerialPort succ, ret=700, dst_type=1, int_msgid=9052, dst_mod=10, data_len=571,msg={"process":[{"data":"A|10|1|100|5"}],"process_memory":[{"data":"A|10|1|100|5"}],"process_handle":[],"process_diskio":[{"data":"A|10|8|5|3"}],"process_netio":[{"data":"A|10|6|2|4"}]}',
                '2030-01-01 00:10:07 F[x] L[INFO]WriteSerialPort succ, ret=700, dst_type=1, int_msgid=9052, dst_mod=10, data_len=571,msg={"process":[{"data":"A|11|1|100|5"}],"process_memory":[{"data":"A|11|1|100|5"}],"process_handle":[],"process_diskio":[{"data":"A|11|9|5|4"}],"process_netio":[{"data":"A|11|7|3|4"}]}',
            ]) + "\n", encoding="utf-8")

            rows = [
                protocol_row(0, 4002, {"msgtype": "4002", "agentstatus": "1", "issysprep": "0"}),
                protocol_row(32, 4002, {"msgtype": "4002", "agentstatus": "1", "issysprep": "0"}),
                protocol_row(305, 9051, {"source": 4, "performance": [{
                    "cpu": 4.0,
                    "mem": {"used": 30.0},
                    "network": [{"data": "AA|3|4|0|0|5|6"}],
                    "disk": "7|2|3",
                }]}),
                protocol_row(312, 9052, {
                    "process": [{"data": "A|10|1|100|5"}],
                    "process_memory": [{"data": "A|10|1|100|5"}],
                    "process_handle": [],
                    "process_diskio": [{"data": "A|10|10|6|4"}],
                    "process_netio": [{"data": "A|10|8|3|5"}],
                    "keyprocess": "A",
                }),
                protocol_row(612, 9052, {
                    "process": [{"data": "A|10|1|100|5"}],
                    "process_memory": [{"data": "A|10|1|100|5"}],
                    "process_handle": [],
                    "process_diskio": [{"data": "A|10|11|7|4"}],
                    "process_netio": [{"data": "A|10|9|4|5"}],
                    "keyprocess": "A",
                }),
            ]
            runtime.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            result = compare([real], [runtime])

        self.assertEqual(
            set(result) - {"inputs"},
            {
                "message_timeline_difference",
                "msgtype_proportions",
                "period_deviation",
                "field_differences",
                "process_trends",
                "metric_distributions",
            },
        )
        heartbeat_period = result["period_deviation"]["guest_to_host:4002"]
        self.assertEqual(heartbeat_period["real_seconds"]["p50"], 30.0)
        self.assertEqual(heartbeat_period["runtime_seconds"]["p50"], 32.0)
        self.assertEqual(heartbeat_period["median_delta_seconds"], 2.0)
        self.assertEqual(result["process_trends"]["real"]["pid_change_count"], 2)
        self.assertEqual(result["process_trends"]["runtime"]["pid_change_count"], 0)
        self.assertEqual(
            result["process_trends"]["runtime"]["process_presence_top10"][0],
            {"name": "A", "snapshot_count": 2, "share": 1.0},
        )
        self.assertEqual(result["metric_distributions"]["real"]["cpu_percent"]["p50"], 2.0)
        self.assertEqual(result["metric_distributions"]["runtime"]["cpu_percent"]["p50"], 4.0)
        fields = result["field_differences"]["guest_to_host:4002"]
        self.assertIn("$.issysprep", fields["runtime_only_paths"])

    def test_runtime_envelope_metrics_are_collected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "empty.log"
            runtime = root / "runtime.jsonl"
            real.write_text("", encoding="utf-8")
            runtime.write_text(json.dumps({
                "observed_at": "2030-01-01T00:00:00+00:00",
                "provider": "windows-validation",
                "metrics": {
                    "cpu": {"percent": 5.0},
                    "memory": {"percent": 40.0},
                    "disk_io": {"activity_rate": 2.0},
                    "network_io": {"tx_bytes_per_second": 3.0, "rx_bytes_per_second": 4.0},
                    "process_snapshot": {
                        "process": [{"name": "A", "pid": 1}],
                        "process_memory": [],
                        "process_handle": [],
                        "process_diskio": [],
                        "process_netio": [],
                    },
                },
            }) + "\n", encoding="utf-8")
            result = compare([real], [runtime])

        distributions = result["metric_distributions"]["runtime"]
        self.assertEqual(distributions["cpu_percent"]["mean"], 5.0)
        self.assertEqual(distributions["network_rx"]["mean"], 4.0)
        self.assertEqual(result["process_trends"]["runtime"]["snapshot_count"], 1)


if __name__ == "__main__":
    unittest.main()
