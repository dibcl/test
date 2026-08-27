from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "lab" / "mock-telemetry"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from telemetry.accelerated_validation import AcceleratedWindowsValidationProvider
from telemetry.clocks import SimulatedClock
from message_adapters.mswitch_frame import HEADER_SIZE, MswitchFrameEncoder, MswitchHeader, decode_serial_frame
from message_adapters.scheduler import TelemetryMessageScheduler
from message_adapters.windows import WindowsMessageEncoder


GROUPS = ("process", "process_memory", "process_handle", "process_diskio", "process_netio")
CORE_NAMES = ("IceDisplay", "IceTunnel", "VmQoEAgent", "MswitchWin")
FROZEN_IDS = {4002, 4004, 9050, 9051, 9052, 9054}
CLASS_A_IDS = {8007, 8059, 9053, 9055, 9056}


def _adapter_config(enabled: bool) -> dict[str, Any]:
    config: dict[str, Any] = {
        "agentversion": "V7.25.21SP3pv",
        "software_profile": str(RUNTIME_ROOT / "fixtures" / "observed-software-baseline.json"),
        "versions": {"vmbooster": "V7.25.21SP3pv", "PVDriver": "3.18.34.723185c6"},
        "environment": {"diskused": "C:29.03GB,D:40.20GB"},
    }
    if enabled:
        config["class_a"] = {
            "enabled": True,
            "evidence_profile": str(RUNTIME_ROOT / "fixtures" / "class-a-observed-baseline.json"),
            "gateway": "172.20.176.1",
        }
    return config


def _class_a_summary(messages: list[Any], uuid: str) -> dict[str, Any]:
    by_id: dict[int, list[Any]] = defaultdict(list)
    encoder = MswitchFrameEncoder(uuid)
    wire_lengths: dict[str, list[float]] = defaultdict(list)
    for message in messages:
        by_id[message.int_msgid].append(message)
        if message.int_msgid in CLASS_A_IDS:
            raw = decode_serial_frame(encoder.encode(message))
            wire_lengths[str(message.int_msgid)].append(float(MswitchHeader.parse(raw).data_len))
    cadence = {}
    for msgid in sorted(CLASS_A_IDS):
        rows = by_id[msgid]
        stamps = [datetime.fromisoformat(row.emitted_at) for row in rows]
        cadence[str(msgid)] = {
            "count": len(rows),
            "interval_mean": _mean([(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]),
            "wire_length_mean": _mean(wire_lengths[str(msgid)]),
            "wire_length_min": min(wire_lengths[str(msgid)]),
            "wire_length_max": max(wire_lengths[str(msgid)]),
        }
    logs = [row for message in by_id[9053] for row in message.payload["logdatas"]]
    states_9056 = [
        message.payload["datas"][0]["row"].rsplit(",", 4)[-4:]
        for message in by_id[9056]
    ]
    variants_8059 = [
        "populated" if "gateway" in message.payload else "minimal"
        for message in by_id[8059]
    ]
    startup_9050 = min(
        (message for message in messages if message.int_msgid == 9050),
        key=lambda value: value.emitted_at,
    )
    startup_9055 = by_id[9055][0]
    return {
        "message_counts": {str(msgid): len(by_id[msgid]) for msgid in sorted(CLASS_A_IDS)},
        "cadence_and_wire": cadence,
        "9055_to_9050_seconds": (
            datetime.fromisoformat(startup_9050.emitted_at)
            - datetime.fromisoformat(startup_9055.emitted_at)
        ).total_seconds(),
        "8059_variant_counts": dict(Counter(variants_8059)),
        "8059_persistence_ratio": (
            sum(a == b for a, b in zip(variants_8059, variants_8059[1:]))
            / (len(variants_8059) - 1)
        ),
        "9053_batch_size": {
            "mean": _mean([float(len(message.payload["logdatas"])) for message in by_id[9053]]),
            "min": min(len(message.payload["logdatas"]) for message in by_id[9053]),
            "max": max(len(message.payload["logdatas"]) for message in by_id[9053]),
            "empty": sum(not message.payload["logdatas"] for message in by_id[9053]),
        },
        "9053_percent_plus_ratio": sum("%" in row["log"] or "+" in row["log"] for row in logs) / len(logs),
        "9056_distinct_states": len({tuple(value) for value in states_9056}),
        "9056_persistence_ratio": (
            sum(a == b for a, b in zip(states_9056, states_9056[1:]))
            / (len(states_9056) - 1)
        ),
    }


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 2 or len(right) < 2:
        return 0.0
    left_mean, right_mean = _mean(left), _mean(right)
    denominator = statistics.pstdev(left) * statistics.pstdev(right)
    return (
        _mean([(x - left_mean) * (y - right_mean) for x, y in zip(left, right)])
        / denominator
        if denominator else 0.0
    )


def _lag1(values: list[float]) -> float:
    return _correlation(values[:-1], values[1:])


def _entropy(values: list[int]) -> float:
    counts = Counter(values)
    total = len(values)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _ranking(rows_by_snapshot: list[list[dict[str, Any]]]) -> dict[str, Any]:
    name_sets: list[set[str]] = []
    identities: set[tuple[str, int]] = set()
    duplicate_slots: list[float] = []
    duplicate_families: list[float] = []
    leaders: list[str] = []
    pid_presence: Counter[tuple[str, int]] = Counter()
    for rows in rows_by_snapshot:
        names = {str(row["name"]) for row in rows}
        name_sets.append(names)
        leaders.append(str(rows[0]["name"]) if rows else "")
        by_name: dict[str, set[int]] = defaultdict(set)
        for row in rows:
            identity = (str(row["name"]), int(row["pid"]))
            identities.add(identity)
            pid_presence[identity] += 1
            by_name[identity[0]].add(identity[1])
        duplicate_slots.append(float(sum(max(0, len(pids) - 1) for pids in by_name.values())))
        duplicate_families.append(float(sum(len(pids) > 1 for pids in by_name.values())))
    jaccard = [
        len(left & right) / len(left | right) if left | right else 1.0
        for left, right in zip(name_sets, name_sets[1:])
    ]
    lifetimes = list(pid_presence.values())
    return {
        "snapshot_count": len(rows_by_snapshot),
        "unique_names": len(set().union(*name_sets)) if name_sets else 0,
        "unique_pids": len(identities),
        "leader_changes": sum(left != right for left, right in zip(leaders, leaders[1:])),
        "unique_leaders": len(set(leaders)),
        "leaders": dict(Counter(leaders)),
        "jaccard_mean": _mean(jaccard),
        "duplicate_slots_mean": _mean(duplicate_slots),
        "duplicate_slots_min": min(duplicate_slots, default=0.0),
        "duplicate_slots_max": max(duplicate_slots, default=0.0),
        "duplicate_slots_std": statistics.pstdev(duplicate_slots) if len(duplicate_slots) > 1 else 0.0,
        "duplicate_families_mean": _mean(duplicate_families),
        "pid_lifetime_mean_snapshots": _mean([float(value) for value in lifetimes]),
        "one_snapshot_pid_ratio": sum(value == 1 for value in lifetimes) / len(lifetimes) if lifetimes else 0.0,
        "at_most_two_snapshot_pid_ratio": sum(value <= 2 for value in lifetimes) / len(lifetimes) if lifetimes else 0.0,
    }


async def _run_seed(seed: int) -> tuple[dict[str, Any], list[int]]:
    with patch("telemetry.accelerated_validation.random.SystemRandom") as system_random:
        system_random.return_value.randrange.return_value = seed
        provider = AcceleratedWindowsValidationProvider(
            RUNTIME_ROOT / "baseline.runtime.json", RUNTIME_ROOT / "local_env.json"
        )
    clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
    cpu: list[float] = []
    memory: list[float] = []
    paged: list[float] = []
    nonpaged: list[float] = []
    core_leaders: list[int] = []
    overall_core_delta: list[float] = []
    disk: dict[str, list[float]] = defaultdict(list)
    rankings: dict[str, list[list[dict[str, Any]]]] = {group: [] for group in GROUPS}
    core_pids: dict[str, set[int]] = {name: set() for name in CORE_NAMES}
    process_metric: dict[str, list[float]] = defaultdict(list)
    class_scheduler = TelemetryMessageScheduler(WindowsMessageEncoder(_adapter_config(True)), {})
    frozen_scheduler = TelemetryMessageScheduler(WindowsMessageEncoder(_adapter_config(False)), {})
    class_messages: list[Any] = []
    class_frozen_messages: list[dict[str, Any]] = []
    baseline_frozen_messages: list[dict[str, Any]] = []
    process_deadlines = {307 + 300 * index for index in range(23)}
    for second in range(7200):
        snapshot = await provider.snapshot(clock)
        current = class_scheduler.messages_for(snapshot, float(second))
        class_messages.extend(current)
        class_frozen_messages.extend(
            message.to_dict() for message in current if message.int_msgid in FROZEN_IDS
        )
        baseline_frozen_messages.extend(
            message.to_dict()
            for message in frozen_scheduler.messages_for(snapshot, float(second))
        )
        if second and second % 60 == 0:
            metrics = snapshot.metrics
            overall = float(metrics["cpu"]["percent"])
            cores = [float(value) for value in metrics["cpu"]["per_core"]]
            cpu.append(overall)
            memory.append(float(metrics["memory"]["percent"]))
            paged.append(float(metrics["memory"]["paged_pool_mb"]))
            nonpaged.append(float(metrics["memory"]["nonpaged_pool_mb"]))
            core_leaders.append(max(range(len(cores)), key=cores.__getitem__))
            overall_core_delta.append(abs(overall - _mean(cores)))
            disk_rows = metrics["disk_io"]["per_disk"]
            disk["read_iops"].append(sum(float(row["read_iops"]) for row in disk_rows))
            disk["write_iops"].append(sum(float(row["write_iops"]) for row in disk_rows))
            disk["read_kbps"].append(sum(float(row["read_kb_per_second"]) for row in disk_rows))
            disk["write_kbps"].append(sum(float(row["write_kb_per_second"]) for row in disk_rows))
            latencies = [
                float(row[key]) for row in disk_rows
                for key in ("read_latency_ms", "write_latency_ms")
            ]
            disk["latency"].append(_mean(latencies))
            disk["queue"].append(sum(float(row["queue_length"]) for row in disk_rows))
        if second in process_deadlines:
            process = snapshot.metrics["process_snapshot"]
            for group in GROUPS:
                rankings[group].append(process[group])
            for row in process["process_memory"]:
                process_metric["memory_working_set_kb"].append(float(row["rss_mb"]) * 1024.0)
                process_metric["memory_handles"].append(float(row["handles"]))
            for row in process["process_handle"]:
                process_metric["handle_handles"].append(float(row["handles"]))
                process_metric["handle_memory_kb"].append(float(row["rss_mb"]) * 1024.0)
                process_metric["handle_cpu"].append(float(row["cpu_percent"]))
            for row in process["process_diskio"]:
                process_metric["disk_total"].append(float(row["disk_io_rate"]))
                process_metric["disk_read"].append(float(row["disk_read_rate"]))
                process_metric["disk_write"].append(float(row["disk_write_rate"]))
            for group in GROUPS:
                for row in process[group]:
                    if row["name"] in core_pids:
                        core_pids[row["name"]].add(int(row["pid"]))
        await clock.sleep(1)
    result = {
        "requested_seed": seed,
        "effective_seed": provider.run_seed,
        "cpu": {"lag1": _lag1(cpu), "min": min(cpu), "max": max(cpu), "mean": _mean(cpu), "std": statistics.pstdev(cpu)},
        "memory": {"lag1": _lag1(memory), "min": min(memory), "max": max(memory), "mean": _mean(memory), "std": statistics.pstdev(memory)},
        "per_core": {
            "leader_entropy_bits": _entropy(core_leaders),
            "overall_mean_absolute_delta": _mean(overall_core_delta),
            "leader_sequence": core_leaders,
        },
        "pool": {
            "paged_mean": _mean(paged), "paged_min": min(paged), "paged_max": max(paged),
            "nonpaged_mean": _mean(nonpaged), "nonpaged_min": min(nonpaged), "nonpaged_max": max(nonpaged),
            "paged_lag1": _lag1(paged), "nonpaged_lag1": _lag1(nonpaged),
        },
        "disk": {
            "read_iops_mean": _mean(disk["read_iops"]),
            "write_iops_mean": _mean(disk["write_iops"]),
            "read_kbps_mean": _mean(disk["read_kbps"]),
            "write_kbps_mean": _mean(disk["write_kbps"]),
            "iops_correlation": _correlation(disk["read_iops"], disk["write_iops"]),
            "throughput_correlation": _correlation(disk["read_kbps"], disk["write_kbps"]),
            "latency_mean": _mean(disk["latency"]),
            "queue_mean": _mean(disk["queue"]),
            "latency_zero_ratio": sum(value < 0.005 for value in disk["latency"]) / len(disk["latency"]),
            "queue_zero_ratio": sum(value < 0.005 for value in disk["queue"]) / len(disk["queue"]),
        },
        "rankings": {group: _ranking(rows) for group, rows in rankings.items()},
        "process_metric_scale": {key: _mean(values) for key, values in process_metric.items()},
        "core_pid_continuity": {name: sorted(pids) for name, pids in core_pids.items()},
        "class_a": _class_a_summary(
            class_messages,
            str(provider.local_environment["UUID"]),
        ),
        "six_message_regression": {
            "exact_match": class_frozen_messages == baseline_frozen_messages,
            "enabled_count": len(class_frozen_messages),
            "baseline_count": len(baseline_frozen_messages),
        },
    }
    return result, core_leaders


async def run(seeds: list[int]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    leader_sequences: list[list[int]] = []
    for seed in seeds:
        row, leaders = await _run_seed(seed)
        rows.append(row)
        leader_sequences.append(leaders)
    agreements = [
        _mean([float(left == right) for left, right in zip(a, b)])
        for index, a in enumerate(leader_sequences)
        for b in leader_sequences[index + 1:]
    ]
    evidence = json.loads(
        (RUNTIME_ROOT / "fixtures" / "class-a-observed-baseline.json").read_text(
            encoding="utf-8"
        )
    )
    class_rows = [row["class_a"] for row in rows]
    count_9053 = [float(row["message_counts"]["9053"]) for row in class_rows]
    batch_9053 = [float(row["9053_batch_size"]["mean"]) for row in class_rows]
    encoded_9053 = [float(row["9053_percent_plus_ratio"]) for row in class_rows]
    validation = {
        "8007": {
            "pass": all(row["message_counts"]["8007"] == 23 for row in class_rows),
            "counts": [row["message_counts"]["8007"] for row in class_rows],
            "real_count_per_hour": evidence["8007"]["count_per_hour"],
        },
        "8059": {
            "pass": all(
                row["message_counts"]["8059"] == 25
                and row["8059_variant_counts"] == {"minimal": 1, "populated": 24}
                for row in class_rows
            ),
            "counts": [row["message_counts"]["8059"] for row in class_rows],
            "real_count_per_hour": evidence["8059"]["count_per_hour"],
        },
        "9053": {
            "pass": (
                0.70 * evidence["9053"]["count_per_hour"] * 2
                <= _mean(count_9053)
                <= 1.30 * evidence["9053"]["count_per_hour"] * 2
                and 1.0 <= _mean(batch_9053) <= 5.5
                and _mean(encoded_9053) >= 0.95
            ),
            "counts": count_9053,
            "count_mean": _mean(count_9053),
            "batch_size_mean_across_seeds": _mean(batch_9053),
            "percent_plus_ratio_mean_across_seeds": _mean(encoded_9053),
            "real_count_per_hour": evidence["9053"]["count_per_hour"],
            "real_batch_size_mean": evidence["9053"]["batch_size"]["mean"],
        },
        "9055": {
            "pass": all(
                row["message_counts"]["9055"] == 1
                and row["9055_to_9050_seconds"] in (1.0, 2.0)
                for row in class_rows
            ),
            "offsets": [row["9055_to_9050_seconds"] for row in class_rows],
        },
        "9056": {
            "pass": all(
                row["message_counts"]["9056"] == 23
                and abs(row["cadence_and_wire"]["9056"]["interval_mean"] - 304.0) < 0.1
                for row in class_rows
            ),
            "counts": [row["message_counts"]["9056"] for row in class_rows],
            "real_count_per_hour": evidence["9056"]["count_per_hour"],
        },
        "six_message_regression": {
            "pass": all(row["six_message_regression"]["exact_match"] for row in rows),
            "all_exact_match": all(row["six_message_regression"]["exact_match"] for row in rows),
        },
    }
    validation["all_class_a_pass"] = all(
        validation[str(msgtype)]["pass"] for msgtype in CLASS_A_IDS
    )
    return {
        "seeds": seeds,
        "cross_seed_core_leader_same_position_agreement": {
            "mean": _mean(agreements),
            "min": min(agreements) if agreements else None,
            "max": max(agreements) if agreements else None,
        },
        "class_a_validation": validation,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", action="append", type=int)
    args = parser.parse_args()
    seeds = args.seed or [1103, 2207, 3301, 4409, 5501, 6607, 7717, 8803, 9901, 11113]
    result = asyncio.run(run(seeds))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
