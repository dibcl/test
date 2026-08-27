"""Run the host-isolated two-hour accelerated validation and write offline artifacts."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import statistics
import struct
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "lab" / "mock-telemetry"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from message_adapters.model import ProtocolMessage
from message_adapters.mswitch_frame import MswitchFrameEncoder
from telemetry.config import load_config, register_transport
from telemetry.runtime import TelemetryRuntime
from telemetry.transports import BaseTransport
from telemetry_log_compare import compare


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0, "min": None, "mean": None, "median": None,
            "p25": None, "p50": None, "p75": None, "p90": None,
            "p95": None, "stddev": None, "max": None,
        }
    ordered = sorted(float(value) for value in values)

    def percentile(probability: float) -> float:
        position = (len(ordered) - 1) * probability
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

    return {
        "count": len(ordered), "min": ordered[0],
        "mean": statistics.fmean(ordered), "median": statistics.median(ordered),
        "p25": percentile(0.25), "p50": percentile(0.50),
        "p75": percentile(0.75), "p90": percentile(0.90),
        "p95": percentile(0.95), "stddev": statistics.pstdev(ordered),
        "max": ordered[-1],
    }


def _range_counts(
    values: list[float], bands: tuple[tuple[str, float, float], ...],
) -> dict[str, dict[str, float | int]]:
    return {
        label: {
            "count": sum(low <= value < high for value in values),
            "ratio": sum(low <= value < high for value in values) / len(values),
        }
        for label, low, high in bands
    }


def _episode_periodicity(values: list[float], threshold: float) -> dict[str, Any]:
    starts = [
        index for index, value in enumerate(values)
        if value >= threshold and (index == 0 or values[index - 1] < threshold)
    ]
    intervals = [float(right - left) for left, right in zip(starts, starts[1:])]
    periodic = False
    if len(intervals) >= 3 and statistics.fmean(intervals) > 0:
        periodic = statistics.pstdev(intervals) / statistics.fmean(intervals) < 0.12
    return {
        "episode_count": len(starts),
        "start_sample_indexes": starts,
        "interval_samples": intervals,
        "fixed_period_detected": periodic,
    }


def _cpu_memory_checks(
    cpu: list[float], memory: list[float], per_core_cpu: list[float]
) -> dict[str, Any]:
    cpu_ordered = sorted(set(cpu))
    memory_ordered = sorted(set(memory))
    cpu_gap = max((right - left for left, right in zip(cpu_ordered, cpu_ordered[1:])), default=0.0)
    memory_gap = max((right - left for left, right in zip(memory_ordered, memory_ordered[1:])), default=0.0)
    cpu_delta = [abs(right - left) for left, right in zip(cpu, cpu[1:])]
    memory_delta = [abs(right - left) for left, right in zip(memory, memory[1:])]
    cpu_mean = statistics.fmean(cpu)
    memory_mean = statistics.fmean(memory)
    covariance = statistics.fmean(
        (left - cpu_mean) * (right - memory_mean) for left, right in zip(cpu, memory)
    )
    denominator = statistics.pstdev(cpu) * statistics.pstdev(memory)
    correlation = covariance / denominator if denominator else 0.0
    cpu_speed = statistics.fmean(cpu_delta) if cpu_delta else 0.0
    memory_speed = statistics.fmean(memory_delta) if memory_delta else 0.0
    return {
        "cpu_ranges": _range_counts(cpu, (
            ("3-10", 3.0, 10.0), ("10-20", 10.0, 20.0),
            ("20-30", 20.0, 30.0), ("30-40", 30.0, 40.0),
            ("40-50", 40.0, 50.0), ("50-60", 50.0, 60.0),
        )),
        "memory_ranges": _range_counts(memory, (
            ("30-35", 30.0, 35.0), ("35-40", 35.0, 40.0),
            ("40-45", 40.0, 45.0), ("45-50", 45.0, 50.0),
            ("50-55", 50.0, 55.0), ("55-60", 55.0, 60.0),
        )),
        "data_holes": {
            "cpu_largest_adjacent_value_gap": cpu_gap,
            "cpu_obvious_hole_detected": cpu_gap > 8.0,
            "memory_largest_adjacent_value_gap": memory_gap,
            "memory_obvious_hole_detected": memory_gap > 5.0,
        },
        "high_value_periodicity": {
            "cpu_50_plus": _episode_periodicity(cpu, 50.0),
            "memory_50_plus": _episode_periodicity(memory, 50.0),
        },
        "fixed_boundary_values": {
            "cpu": {str(value): cpu.count(value) for value in (3.0, 58.49, 58.5, 60.0)},
            "per_core_cpu": {
                str(value): per_core_cpu.count(value)
                for value in (3.0, 58.49, 58.5, 60.0)
            },
            "memory": {
                str(value): memory.count(value)
                for value in (30.0, 58.49, 58.5, 60.0)
            },
            "detected": any(cpu.count(value) > 1 for value in (3.0, 58.49, 58.5, 60.0))
            or any(per_core_cpu.count(value) > 1 for value in (3.0, 58.49, 58.5, 60.0))
            or any(memory.count(value) > 1 for value in (30.0, 58.49, 58.5, 60.0)),
        },
        "cpu_memory_correlation": correlation,
        "correlation_too_high": abs(correlation) >= 0.80,
        "mean_absolute_sample_delta": {"cpu": cpu_speed, "memory": memory_speed},
        "memory_slower_than_cpu": memory_speed < cpu_speed,
        "memory_to_cpu_speed_ratio": memory_speed / cpu_speed if cpu_speed else None,
    }


def _frame_length_summary(path: Path) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[int, list[float]] = {}
    decoded = bytearray()
    escaped = False
    for value in path.read_bytes():
        if escaped:
            decoded.append(value)
            escaped = False
        elif value == 0x5C:
            escaped = True
        elif value == 0x3B:
            msgid = struct.unpack_from("<I", decoded, 0x50)[0]
            data_len = struct.unpack_from("<I", decoded, 0x5C)[0]
            grouped.setdefault(msgid, []).append(float(data_len))
            decoded.clear()
        else:
            decoded.append(value)
    return {str(key): _stats(value) for key, value in sorted(grouped.items())}


def _millisecond_distribution(messages: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[int]] = {}
    performance: list[int] = []
    for item in messages:
        msgid = str(item["int_msgid"])
        grouped.setdefault(msgid, []).append(
            datetime.fromisoformat(str(item["emitted_at"])).microsecond // 1000
        )
        if int(item["int_msgid"]) == 9051:
            performance.extend(
                datetime.fromisoformat(str(sample["createtime"])).microsecond // 1000
                for sample in item["payload"]["performance"]
            )

    def describe(values: list[int]) -> dict[str, Any]:
        counts = Counter(values)
        circular_increments = [
            float((right - left) % 1000) for left, right in zip(values, values[1:])
        ]
        return {
            "count": len(values),
            "distinct_count": len(counts),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "span": max(values) - min(values) if values else None,
            "stddev": statistics.pstdev(values) if len(values) > 1 else 0.0,
            "circular_phase_increment": _stats(circular_increments),
            "most_common": [
                {"millisecond": value, "count": count}
                for value, count in counts.most_common(10)
            ],
        }

    return {
        "emitted_at_by_msgid": {
            key: describe(values) for key, values in sorted(grouped.items())
        },
        "performance_createtime": describe(performance),
    }


def _paired_9051_9052_delays(messages: list[dict[str, Any]]) -> dict[str, Any]:
    performance = [
        datetime.fromisoformat(str(item["emitted_at"]))
        for item in messages if int(item["int_msgid"]) == 9051
    ]
    process = [
        datetime.fromisoformat(str(item["emitted_at"]))
        for item in messages if int(item["int_msgid"]) == 9052
    ]
    return _stats([
        (process_time - performance_time).total_seconds()
        for performance_time, process_time in zip(performance, process)
    ])


def _process_ecology(messages: list[dict[str, Any]]) -> dict[str, Any]:
    groups = (
        "process", "process_memory", "process_handle", "process_diskio", "process_netio"
    )
    snapshots = [
        item["payload"] for item in messages if int(item["int_msgid"]) == 9052
    ]
    result: dict[str, Any] = {}
    for group in groups:
        name_sets: list[set[str]] = []
        pids_by_name: dict[str, set[int]] = {}
        duplicate_slots: list[float] = []
        duplicate_families: list[float] = []
        for snapshot in snapshots:
            names: set[str] = set()
            snapshot_pids: dict[str, set[int]] = {}
            for row in snapshot[group]:
                fields = str(row["data"]).split("|")
                name = fields[0]
                pid = int(fields[1])
                names.add(name)
                pids_by_name.setdefault(name, set()).add(pid)
                snapshot_pids.setdefault(name, set()).add(pid)
            name_sets.append(names)
            duplicate_slots.append(float(sum(
                max(0, len(values) - 1) for values in snapshot_pids.values()
            )))
            duplicate_families.append(float(sum(
                len(values) > 1 for values in snapshot_pids.values()
            )))
        jaccards = [
            len(left & right) / len(left | right) if left | right else 1.0
            for left, right in zip(name_sets, name_sets[1:])
        ]
        result[group] = {
            "snapshot_count": len(name_sets),
            "unique_process_names": len(pids_by_name),
            "unique_pids": len({pid for values in pids_by_name.values() for pid in values}),
            "same_name_multiple_pid_count": sum(len(values) > 1 for values in pids_by_name.values()),
            "same_name_multiple_pid_names": sorted(
                name for name, values in pids_by_name.items() if len(values) > 1
            ),
            "concurrent_same_name_multi_pid_snapshot_count": sum(
                value > 0 for value in duplicate_slots
            ),
            "concurrent_same_name_multi_pid_snapshot_ratio": (
                sum(value > 0 for value in duplicate_slots) / len(duplicate_slots)
                if duplicate_slots else 0.0
            ),
            "duplicate_slots_per_snapshot": _stats(duplicate_slots),
            "duplicate_family_count_per_snapshot": _stats(duplicate_families),
            "consecutive_process_set_jaccard": _stats(jaccards),
        }
    core = ("IceDisplay", "IceTunnel", "VmQoEAgent", "MswitchWin")
    core_pids: dict[str, set[int]] = {name: set() for name in core}
    for snapshot in snapshots:
        for group in groups:
            for row in snapshot[group]:
                fields = str(row["data"]).split("|")
                if fields[0] in core_pids:
                    core_pids[fields[0]].add(int(fields[1]))
    result["core_pid_counts"] = {name: len(values) for name, values in core_pids.items()}
    return result


class OfflineCaptureTransport(BaseTransport):
    def __init__(self, output: Path, uuid: str, cutoff: datetime) -> None:
        self.output = output
        self.encoder = MswitchFrameEncoder(uuid, dst_type=1)
        self.cutoff = cutoff
        self.messages: list[dict[str, Any]] = []
        self._json = None
        self._raw = None

    async def open(self) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        self._json = (self.output / "messages.jsonl").open("w", encoding="utf-8", buffering=1)
        self._raw = (self.output / "mswitch.raw").open("wb")

    async def send(self, message: dict[str, Any]) -> None:
        if datetime.fromisoformat(str(message["emitted_at"])) >= self.cutoff:
            return
        payload = message["payload"]
        protocol = ProtocolMessage(
            int(message["int_msgid"]), int(message["source_module"]),
            int(message["destination_module"]), str(message["emitted_at"]), payload,
        )
        self._json.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._raw.write(self.encoder.encode(protocol))
        self._json.flush()
        self._raw.flush()
        self.messages.append(message)

    async def close(self) -> None:
        if self._json:
            self._json.close()
        if self._raw:
            self._raw.close()


def _absolute_config(path: Path) -> dict[str, Any]:
    config = load_config(path)
    base = path.parent
    for key in ("profile", "local_env"):
        config["provider"][key] = str((base / config["provider"][key]).resolve())
    config["message_adapter"]["software_profile"] = str(
        (base / config["message_adapter"]["software_profile"]).resolve()
    )
    class_a = config["message_adapter"].get("class_a")
    if isinstance(class_a, dict) and isinstance(class_a.get("evidence_profile"), str):
        class_a["evidence_profile"] = str(
            (base / class_a["evidence_profile"]).resolve()
        )
    return config


def _summary(
    messages: list[dict[str, Any]], real_seconds: float, window_seconds: float,
) -> dict[str, Any]:
    times = [datetime.fromisoformat(item["emitted_at"]) for item in messages]
    by_id: dict[int, list[datetime]] = {}
    for item, stamp in zip(messages, times):
        by_id.setdefault(int(item["int_msgid"]), []).append(stamp)
    periods = {}
    for msgid, stamps in by_id.items():
        periods[str(msgid)] = _stats([
            (right - left).total_seconds() for left, right in zip(stamps, stamps[1:])
        ])

    metric: dict[str, list[float]] = {
        "cpu": [], "per_core_cpu": [], "memory": [], "network_tx": [], "network_rx": [],
        "disk_activity": [], "disk_read_iops": [], "disk_write_iops": [],
        "disk_read_kb_per_second": [], "disk_write_kb_per_second": [],
    }
    rank_groups = {key: [] for key in ("process", "process_memory", "process_handle", "process_diskio", "process_netio")}
    for item in messages:
        payload = item["payload"]
        if int(item["int_msgid"]) == 9051:
            for sample in payload["performance"]:
                metric["cpu"].append(float(sample["cpu"]))
                metric["per_core_cpu"].extend(
                    float(row["data"].split("|")[1]) for row in sample["cpus"]
                )
                metric["memory"].append(float(sample["mem"]["used"]))
                network = sample["network"][0]["data"].split("|")
                metric["network_tx"].append(float(network[1]))
                metric["network_rx"].append(float(network[2]))
                disk = [float(value) for value in sample["disk"].split("|")]
                metric["disk_activity"].append(disk[0])
                metric["disk_read_iops"].append(disk[3])
                metric["disk_write_iops"].append(disk[4])
                metric["disk_read_kb_per_second"].append(disk[5])
                metric["disk_write_kb_per_second"].append(disk[6])
        elif int(item["int_msgid"]) == 9052:
            for group in rank_groups:
                rows = payload[group]
                rank_groups[group].append(rows[0]["data"].split("|")[0] if rows else None)
    return {
        "simulated_start": times[0].isoformat(),
        "simulated_end": (times[0] + timedelta(seconds=window_seconds)).isoformat(),
        "last_message_time": times[-1].isoformat(),
        "real_elapsed_seconds": real_seconds,
        "simulated_duration_seconds": window_seconds,
        "message_count": len(messages),
        "message_counts": dict(sorted(Counter(str(item["int_msgid"]) for item in messages).items())),
        "period_seconds": periods,
        "distributions": {key: _stats(value) for key, value in metric.items()},
        "cpu_memory_analysis": _cpu_memory_checks(
            metric["cpu"], metric["memory"], metric["per_core_cpu"]
        ),
        "process_rank_changes": {
            key: sum(left != right for left, right in zip(values, values[1:]))
            for key, values in rank_groups.items()
        },
    }


def _class_a_summary(
    messages: list[dict[str, Any]], frame_lengths: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    ids = (8007, 8059, 9053, 9055, 9056)
    by_id = {
        msgid: [item for item in messages if int(item["int_msgid"]) == msgid]
        for msgid in ids
    }
    periods = {}
    for msgid, rows in by_id.items():
        stamps = [datetime.fromisoformat(item["emitted_at"]) for item in rows]
        periods[str(msgid)] = _stats([
            (right - left).total_seconds() for left, right in zip(stamps, stamps[1:])
        ])

    variants_8059 = [
        "populated" if "gateway" in item["payload"] else "minimal"
        for item in by_id[8059]
    ]
    logs_9053 = [
        str(row["log"])
        for item in by_id[9053]
        for row in item["payload"].get("logdatas", [])
    ]
    batches_9053 = [len(item["payload"].get("logdatas", [])) for item in by_id[9053]]
    categories_9053: Counter[str] = Counter()
    for raw in logs_9053:
        decoded = unquote_plus(raw) if "%" in raw or "+" in raw else raw
        fields = decoded.split("|")
        if len(fields) >= 5:
            categories_9053["|".join(fields[2:5])] += 1

    row_states_9056: list[str] = []
    row_lags_9056: list[float] = []
    identity_9056: list[dict[str, str]] = []
    for item in by_id[9056]:
        row = next(csv.reader(
            [str(item["payload"]["datas"][0]["row"])],
            skipinitialspace=True, quotechar="'",
        ))
        if len(row) == 9:
            row_states_9056.append("|".join(row[5:9]))
            row_lags_9056.append(
                (datetime.fromisoformat(row[0]) - datetime.fromisoformat(item["emitted_at"])).total_seconds()
            )
            identity_9056.append({
                "source_id": row[3], "source_ip": row[4], "gateway_status": row[6]
            })

    startup_9050 = next(item for item in messages if int(item["int_msgid"]) == 9050)
    offset_9055 = (
        datetime.fromisoformat(startup_9050["emitted_at"])
        - datetime.fromisoformat(by_id[9055][0]["emitted_at"])
    ).total_seconds()
    result = {
        "counts": {str(msgid): len(by_id[msgid]) for msgid in ids},
        "count_per_simulated_hour": {str(msgid): len(by_id[msgid]) / 2.0 for msgid in ids},
        "cadence_seconds": periods,
        "wire_payload_length": {str(msgid): frame_lengths.get(str(msgid), {}) for msgid in ids},
        "8007": {
            "all_payloads_exact_observed_state": all(
                item["payload"] == {"msgtype": "8007", "rdp": "0"}
                for item in by_id[8007]
            ),
        },
        "8059": {
            "variant_counts": dict(Counter(variants_8059)),
            "persistence_ratio": sum(a == b for a, b in zip(variants_8059, variants_8059[1:])) / (len(variants_8059) - 1),
        },
        "9053": {
            "batch_size": _stats([float(value) for value in batches_9053]),
            "empty_batches": sum(value == 0 for value in batches_9053),
            "log_event_count": len(logs_9053),
            "percent_plus_ratio": sum("%" in value or "+" in value for value in logs_9053) / len(logs_9053),
            "category_codes": dict(categories_9053),
            "contains_observed_historical_text": any(
                marker in value
                for value in logs_9053
                for marker in (
                    "Authentication+Successed", "USB%5FCAMERA", "Vm+has+run",
                    "Display+Channel+Link+Success", "Agent+stopped",
                )
            ),
        },
        "9055": {"to_9050_seconds": offset_9055, "payload": by_id[9055][0]["payload"]},
        "9056": {
            "state_counts": dict(Counter(row_states_9056)),
            "persistence_ratio": sum(a == b for a, b in zip(row_states_9056, row_states_9056[1:])) / (len(row_states_9056) - 1),
            "row_time_minus_emitted_seconds": _stats(row_lags_9056),
            "identity_values": {
                key: sorted({row[key] for row in identity_9056})
                for key in ("source_id", "source_ip", "gateway_status")
            },
        },
        "real_evidence_reference": {
            str(msgid): {
                "count": evidence[str(msgid)]["count"],
                "count_per_hour": evidence[str(msgid)]["count_per_hour"],
                "cadence_seconds": evidence[str(msgid)].get("cadence_seconds"),
                "payload_length": evidence[str(msgid)]["payload_length"],
            }
            for msgid in ids
        },
    }
    result["assessment"] = {
        "8007": result["8007"]["all_payloads_exact_observed_state"] and result["counts"]["8007"] == 23,
        "8059": result["8059"]["variant_counts"] == {"minimal": 1, "populated": 24},
        "9053": (
            result["counts"]["9053"] >= 1
            and not result["9053"]["contains_observed_historical_text"]
            and result["9053"]["batch_size"]["max"] <= 45
            and result["9053"]["percent_plus_ratio"] >= 0.90
        ),
        "9055": result["counts"]["9055"] == 1 and offset_9055 in (1.0, 2.0),
        "9056": (
            result["counts"]["9056"] == 23
            and abs(result["cadence_seconds"]["9056"]["mean"] - 304.0) < 1.0
            and result["9056"]["persistence_ratio"] >= 0.99
        ),
    }
    result["assessment"]["all_pass"] = all(result["assessment"].values())
    return result


def _six_message_regression(summary: dict[str, Any]) -> dict[str, Any]:
    expected = {"4002": 240, "4004": 1, "9050": 1, "9051": 23, "9052": 23, "9054": 3}
    actual = {key: int(summary["message_counts"].get(key, 0)) for key in expected}
    checks = {
        "counts_exact": actual == expected,
        "4002_cadence": abs(summary["period_seconds"]["4002"]["mean"] - 30.0) < 0.1,
        "9051_cadence": abs(summary["period_seconds"]["9051"]["mean"] - 300.0) < 0.1,
        "9052_cadence": abs(summary["period_seconds"]["9052"]["mean"] - 300.0) < 0.2,
        "9051_9052_pair": abs(summary["paired_9051_9052_delay_seconds"]["mean"] - 7.03) < 0.02,
        "9050_wire_538": summary["frame_payload_length"]["9050"]["min"] == 538,
    }
    return {"expected_counts": expected, "actual_counts": actual, "checks": checks, "pass": all(checks.values())}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _real_source_provenance(
    real_logs: list[Path], package_relative_paths: list[Path] | None = None,
) -> list[dict[str, str]]:
    if package_relative_paths is not None and len(package_relative_paths) != len(real_logs):
        raise ValueError("--real-package-relative must be supplied once per --real-log")
    aliases = package_relative_paths or [
        Path("02_real_fresh_2h") / path.name for path in real_logs
    ]
    return [
        {
            "original_source_path": str(path.resolve()),
            "package_relative_path": alias.as_posix(),
            "sha256": _sha256(path),
        }
        for path, alias in zip(real_logs, aliases)
    ]


def _write_report(
    output: Path,
    real_logs: list[Path],
    real_sources: list[dict[str, str]],
    summary: dict[str, Any],
) -> None:
    result = compare(real_logs, [output / "messages.jsonl"])
    result["accelerated_summary"] = summary
    result["provenance"] = {
        "effective_seed": summary["run_seed"],
        "generated_from": str((output / "messages.jsonl").resolve()),
        "messages_sha256": summary["provenance"]["messages"]["sha256"],
        "mswitch_raw_sha256": summary["provenance"]["mswitch_raw"]["sha256"],
        "summary_source": str((output / "summary.json").resolve()),
        "real_sources": real_sources,
    }
    counts = summary["message_counts"]
    result["assessment"] = {
        "message_count_close": abs(int(counts.get("4002", 0)) - 240) <= 1,
        "cadence_drift_fixed": abs(summary["period_seconds"]["4002"]["mean"] - 30.0) < 0.1,
        "cpu_usage_envelope": {
            "soft_min": 3.0, "soft_upper": 58.5,
            "observed_min": summary["distributions"]["cpu"]["min"],
            "observed_max": summary["distributions"]["cpu"]["max"],
        },
        "memory_usage_envelope": {
            "soft_min": 30.0, "soft_upper": 58.5,
            "observed_min": summary["distributions"]["memory"]["min"],
            "observed_max": summary["distributions"]["memory"]["max"],
        },
        "real_cpu_memory_reference_policy": "protocol-and-shape-reference-only; absolute means are not fit targets",
        "offline_only": True,
        "provider": "windows-validation-accelerated",
    }
    (output / "accelerated_compare_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    d = summary["distributions"]
    natural = summary["cpu_memory_analysis"]
    markdown = f"""# Accelerated 2h comparison

- Message count: {summary['message_count']} ({summary['message_counts']})
- Effective seed: {summary['run_seed']}
- Generated from: {summary['provenance']['messages']['path']}
- Messages SHA256: {summary['provenance']['messages']['sha256']}
- Mswitch raw SHA256: {summary['provenance']['mswitch_raw']['sha256']}
- 4002 cadence: mean {summary['period_seconds']['4002']['mean']:.3f}s, stddev {summary['period_seconds']['4002']['stddev']:.3f}s
- CPU: mean {d['cpu']['mean']:.3f}, min {d['cpu']['min']:.3f}, max {d['cpu']['max']:.3f}
- Memory: mean {d['memory']['mean']:.3f}, min {d['memory']['min']:.3f}, max {d['memory']['max']:.3f}
- Network TX/RX means: {d['network_tx']['mean']:.3f} / {d['network_rx']['mean']:.3f}
- Disk read/write throughput means: {d['disk_read_kb_per_second']['mean']:.3f} / {d['disk_write_kb_per_second']['mean']:.3f}
- Process rank changes: {summary['process_rank_changes']}
- CPU obvious data hole: {natural['data_holes']['cpu_obvious_hole_detected']}
- Memory obvious data hole: {natural['data_holes']['memory_obvious_hole_detected']}
- CPU fixed-period high values: {natural['high_value_periodicity']['cpu_50_plus']['fixed_period_detected']}
- Memory fixed-period high values: {natural['high_value_periodicity']['memory_50_plus']['fixed_period_detected']}
- Fixed boundary values detected: {natural['fixed_boundary_values']['detected']}
- CPU/Memory correlation: {natural['cpu_memory_correlation']:.4f}
- Memory/CPU change-speed ratio: {natural['memory_to_cpu_speed_ratio']:.4f}

## Fixed

- Absolute virtual deadlines remove cumulative scheduler drift.
- Startup order, 4004 +29s delay, 9051 five-sample batching, and 9052 +7s offset are retained.
- CPU cores are independent, network rate/interval fields are distinct, disk fields carry capacity plus sparse IO, and process PIDs remain stable while rankings change.
- Accelerated provider has no live CPU, memory, disk, process, or network input and writes only offline JSONL/mock frames.
- CPU and memory are evaluated as continuous stochastic envelopes with soft bounds (CPU about 3-58.5%, memory about 30-58.5%), not against the absolute means of one idle real-cloud capture.

## Remaining synthetic characteristics

- State transitions and process pool are deterministic fixtures.
- The two-hour sample has fewer organic one-off processes than a live desktop.
- Rare IO/network spikes are modeled rather than workload-driven.
"""
    (output / "accelerated_compare_report.md").write_text(markdown, encoding="utf-8")


def _write_class_a_reports(output: Path, summary: dict[str, Any]) -> None:
    result = {
        "generated_from": str((output / "messages.jsonl").resolve()),
        "effective_seed": summary["run_seed"],
        "class_a": summary["class_a"],
        "six_message_regression": summary["six_message_regression"],
    }
    (output / "class_a_compare_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    assessment = summary["class_a"]["assessment"]
    markdown = f"""# Target B Class A offline comparison

- Effective seed: {summary['run_seed']}
- Generated from: `{(output / 'messages.jsonl').resolve()}`
- Counts: {summary['class_a']['counts']}
- 8007: {'PASS' if assessment['8007'] else 'FAIL'}
- 8059: {'PASS' if assessment['8059'] else 'FAIL'}
- 9053: {'PASS' if assessment['9053'] else 'FAIL'}
- 9055: {'PASS' if assessment['9055'] else 'FAIL'}
- 9056: {'PASS' if assessment['9056'] else 'FAIL'}
- Six-message regression: {'PASS' if summary['six_message_regression']['pass'] else 'FAIL'}

All Class A values are generated from the current virtual clock, local identity,
provider behavior state, and a dedicated RNG domain derived from the same run seed.
No real Host interaction, response, replay, or historical log text is used.
"""
    (output / "class_a_compare_report.md").write_text(markdown, encoding="utf-8")
    (output / "six_message_regression.json").write_text(
        json.dumps(summary["six_message_regression"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def run(args: argparse.Namespace) -> None:
    config = _absolute_config(args.config.resolve())
    local_env = json.loads(Path(config["provider"]["local_env"]).read_text(encoding="utf-8"))
    virtual_start = datetime.fromisoformat(config["clock"]["start"])
    cutoff = (virtual_start + timedelta(seconds=float(config["duration_seconds"]))).replace(
        tzinfo=None
    )
    capture = OfflineCaptureTransport(args.output.resolve(), local_env["UUID"], cutoff)
    register_transport("accelerated_capture", lambda _cfg: capture, replace=True)
    config["transport"] = {"type": "accelerated_capture"}
    runtime = TelemetryRuntime(config)
    wall_started_at = datetime.now().astimezone()
    wall_start = time.perf_counter()
    await runtime.run()
    real_elapsed = time.perf_counter() - wall_start
    wall_ended_at = datetime.now().astimezone()
    real_sources = _real_source_provenance(
        args.real_log, getattr(args, "real_package_relative", None)
    )
    summary = _summary(capture.messages, real_elapsed, float(config["duration_seconds"]))
    summary["frame_payload_length"] = _frame_length_summary(args.output / "mswitch.raw")
    summary["timestamp_milliseconds"] = _millisecond_distribution(capture.messages)
    summary["paired_9051_9052_delay_seconds"] = _paired_9051_9052_delays(
        capture.messages
    )
    summary["process_ecology"] = _process_ecology(capture.messages)
    class_a_profile = json.loads(
        Path(config["message_adapter"]["class_a"]["evidence_profile"]).read_text(
            encoding="utf-8"
        )
    )
    summary["class_a"] = _class_a_summary(
        capture.messages, summary["frame_payload_length"], class_a_profile
    )
    summary["six_message_regression"] = _six_message_regression(summary)
    summary["run_seed"] = getattr(runtime.agent.provider, "run_seed", None)
    summary["wall_started_at"] = wall_started_at.isoformat()
    summary["wall_ended_at"] = wall_ended_at.isoformat()
    summary["provenance"] = {
        "effective_seed": summary["run_seed"],
        "generated_from": str((args.output / "messages.jsonl").resolve()),
        "messages": {
            "path": str((args.output / "messages.jsonl").resolve()),
            "sha256": _sha256(args.output / "messages.jsonl"),
        },
        "mswitch_raw": {
            "path": str((args.output / "mswitch.raw").resolve()),
            "sha256": _sha256(args.output / "mswitch.raw"),
        },
        "real_sources": real_sources,
    }
    (args.output / "runtime-status.json").write_text(
        json.dumps(runtime.status.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(args.output, args.real_log, real_sources, summary)
    _write_class_a_reports(args.output, summary)
    provenance = {
        "effective_seed": summary["run_seed"],
        "messages": summary["provenance"]["messages"],
        "mswitch_raw": summary["provenance"]["mswitch_raw"],
        "real_sources": real_sources,
        "summary": {
            "path": str((args.output / "summary.json").resolve()),
            "sha256": _sha256(args.output / "summary.json"),
            "generated_from": str((args.output / "messages.jsonl").resolve()),
        },
        "validation_report": {
            "path": str((args.output / "accelerated_compare_report.json").resolve()),
            "sha256": _sha256(args.output / "accelerated_compare_report.json"),
            "generated_from": [
                str((args.output / "messages.jsonl").resolve()),
                str((args.output / "summary.json").resolve()),
                *[source["package_relative_path"] for source in real_sources],
            ],
        },
    }
    (args.output / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=RUNTIME_ROOT / "config.windows-validation-accelerated.json")
    parser.add_argument("--output", type=Path, default=RUNTIME_ROOT / "out" / "accelerated-2h")
    parser.add_argument("--real-log", type=Path, action="append", required=True)
    parser.add_argument(
        "--real-package-relative", type=Path, action="append",
        help="package-relative alias paired positionally with each --real-log",
    )
    args = parser.parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
