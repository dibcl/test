from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


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
    return -sum(
        (count / len(values)) * math.log2(count / len(values))
        for count in counts.values()
    ) if values else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(messages: list[dict[str, Any]], group: str) -> list[list[list[str]]]:
    return [
        [str(row["data"]).split("|") for row in item["payload"][group]]
        for item in messages if int(item["int_msgid"]) == 9052
    ]


def build(
    messages_path: Path,
    summary_path: Path,
    matrix_path: Path,
    compare_path: Path,
    package_root: Path | None,
) -> dict[str, Any]:
    messages = [json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines()]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    compare = json.loads(compare_path.read_text(encoding="utf-8"))
    performance = [
        sample
        for item in messages if int(item["int_msgid"]) == 9051
        for sample in item["payload"]["performance"]
    ]
    cpu = [float(sample["cpu"]) for sample in performance]
    memory = [float(sample["mem"]["used"]) for sample in performance]
    paged = [float(sample["mem"]["pagedpool"]) for sample in performance]
    nonpaged = [float(sample["mem"]["nonpagedpool"]) for sample in performance]
    cores = [
        [float(row["data"].split("|")[1]) for row in sample["cpus"]]
        for sample in performance
    ]
    core_leaders = [max(range(len(row)), key=row.__getitem__) for row in cores]
    disk = [[float(value) for value in sample["disk"].split("|")] for sample in performance]
    process_rows = _rows(messages, "process")
    memory_rows = _rows(messages, "process_memory")
    handle_rows = _rows(messages, "process_handle")
    disk_rows = _rows(messages, "process_diskio")
    leaders = [rows[0][0] for rows in process_rows]

    logical = matrix["results"]
    real_sources = summary["provenance"]["real_sources"]
    real_hash_checks = []
    for source in real_sources:
        source_path = (
            package_root / source["package_relative_path"]
            if package_root is not None else Path(source["original_source_path"])
        )
        real_hash_checks.append({
            **source,
            "verified_path": str(source_path),
            "exists": source_path.is_file(),
            "hash_matches": source_path.is_file() and _sha256(source_path) == source["sha256"],
        })

    formal_process_pid_count = len({(row[0], int(row[1])) for rows in process_rows for row in rows})
    compare_pid_count = compare["process_trends"]["runtime"]["unique_pid_count"]
    result: dict[str, Any] = {
        "formal_seed": summary["run_seed"],
        "formal_messages_sha256": _sha256(messages_path),
        "formal_mswitch_raw_sha256": summary["provenance"]["mswitch_raw"]["sha256"],
        "D1": {
            "status": "PASS",
            "formal_cpu_lag1": _lag1(cpu),
            "formal_memory_lag1": _lag1(memory),
            "ten_seed_cpu_lag1_range": [min(row["cpu"]["lag1"] for row in logical), max(row["cpu"]["lag1"] for row in logical)],
            "ten_seed_memory_lag1_range": [min(row["memory"]["lag1"] for row in logical), max(row["memory"]["lag1"] for row in logical)],
            "real_envelope": {"cpu": [-0.050, 0.786], "memory": [0.287, 0.985]},
        },
        "D2": {
            "status": "PASS",
            "formal_leader_entropy_bits": _entropy(core_leaders),
            "ten_seed_entropy_range": [min(row["per_core"]["leader_entropy_bits"] for row in logical), max(row["per_core"]["leader_entropy_bits"] for row in logical)],
            "cross_seed_same_position_agreement": matrix["cross_seed_core_leader_same_position_agreement"],
            "overall_core_mean_absolute_delta": _mean([abs(float(sample["cpu"]) - _mean(row)) for sample, row in zip(performance, cores)]),
        },
        "D3": {
            "status": "PASS",
            "formal": {"paged_mean": _mean(paged), "paged_range": [min(paged), max(paged)], "nonpaged_mean": _mean(nonpaged), "nonpaged_range": [min(nonpaged), max(nonpaged)]},
            "ten_seed_paged_mean_range": [min(row["pool"]["paged_mean"] for row in logical), max(row["pool"]["paged_mean"] for row in logical)],
            "ten_seed_nonpaged_mean_range": [min(row["pool"]["nonpaged_mean"] for row in logical), max(row["pool"]["nonpaged_mean"] for row in logical)],
        },
        "D4": {
            "status": "PASS",
            "formal_write_iops_mean": _mean([row[4] for row in disk]),
            "formal_write_throughput_mean": _mean([row[6] for row in disk]),
            "formal_iops_correlation": _correlation([row[3] for row in disk], [row[4] for row in disk]),
            "formal_throughput_correlation": _correlation([row[5] for row in disk], [row[6] for row in disk]),
            "formal_latency_zero_ratio": sum(row[7] == 0 for row in disk) / len(disk),
            "formal_queue_zero_ratio": sum(row[8] == 0 for row in disk) / len(disk),
        },
        "D5": {
            "status": "PASS",
            "formal_process_memory": {
                "working_set_kb_mean": _mean([float(row[3]) for rows in memory_rows for row in rows]),
                "handles_mean": _mean([float(row[4]) for rows in memory_rows for row in rows]),
            },
            "formal_process_handle": {
                "handles_mean": _mean([float(row[4]) for rows in handle_rows for row in rows]),
                "memory_kb_mean": _mean([float(row[3]) for rows in handle_rows for row in rows]),
                "cpu_mean": _mean([float(row[2]) for rows in handle_rows for row in rows]),
            },
        },
        "D6": {
            "status": "PASS",
            "formal_total_mean": _mean([float(row[2]) for rows in disk_rows for row in rows]),
            "formal_read_mean": _mean([float(row[3]) for rows in disk_rows for row in rows]),
            "formal_max_total_component_error": max(abs(float(row[2]) - float(row[3]) - float(row[4])) for rows in disk_rows for row in rows),
        },
        "D7": {
            "status": "PASS",
            "formal_unique_leaders": len(set(leaders)),
            "formal_leader_changes": sum(left != right for left, right in zip(leaders, leaders[1:])),
            "formal_leader_distribution": dict(Counter(leaders)),
            "ten_seed_unique_leaders": [row["rankings"]["process"]["unique_leaders"] for row in logical],
        },
        "D8": {
            "status": "PASS" if formal_process_pid_count == compare_pid_count else "FAIL",
            "authoritative_unique_process_pids": formal_process_pid_count,
            "compare_tool_unique_process_pids": compare_pid_count,
            "compare_pid_change_share": compare["process_trends"]["runtime"]["pid_change_share"],
        },
        "D9": {
            "status": "PASS" if all(row["hash_matches"] for row in real_hash_checks) else "FAIL",
            "real_sources": real_hash_checks,
            "synthetic_hashes": summary["provenance"],
        },
        "unresolved_needs_more_evidence": [
            "official reserved header/checksum/escaping byte equivalence",
            "4002 sub-second jitter/autocorrelation",
        ],
    }
    result["D1"]["status"] = "PASS" if (
        -0.050 <= result["D1"]["formal_cpu_lag1"] <= 0.786
        and 0.287 <= result["D1"]["formal_memory_lag1"] <= 0.985
        and all(-0.050 <= row["cpu"]["lag1"] <= 0.786 for row in logical)
        and all(0.287 <= row["memory"]["lag1"] <= 0.985 for row in logical)
    ) else "FAIL"
    result["D2"]["status"] = "PASS" if (
        result["D2"]["formal_leader_entropy_bits"] <= 2.362
        and all(row["per_core"]["leader_entropy_bits"] <= 2.362 for row in logical)
        and matrix["cross_seed_core_leader_same_position_agreement"]["mean"] <= 0.374
        and result["D2"]["overall_core_mean_absolute_delta"] <= 0.06
    ) else "FAIL"
    result["D3"]["status"] = "PASS" if (
        max(row["pool"]["paged_mean"] for row in logical)
        - min(row["pool"]["paged_mean"] for row in logical) >= 75.0
        and max(row["pool"]["nonpaged_mean"] for row in logical)
        - min(row["pool"]["nonpaged_mean"] for row in logical) >= 25.0
    ) else "FAIL"
    result["D4"]["status"] = "PASS" if (
        result["D4"]["formal_write_iops_mean"] >= 2.173
        and result["D4"]["formal_write_throughput_mean"] >= 19.631
        and result["D4"]["formal_iops_correlation"] <= 0.660
        and result["D4"]["formal_throughput_correlation"] <= 0.473
        and result["D4"]["formal_latency_zero_ratio"] <= 0.642
        and result["D4"]["formal_queue_zero_ratio"] <= 0.692
        and all(row["disk"]["write_iops_mean"] >= 2.173 for row in logical)
        and all(row["disk"]["write_kbps_mean"] >= 19.631 for row in logical)
    ) else "FAIL"
    result["D5"]["status"] = "PASS" if (
        result["D5"]["formal_process_memory"]["working_set_kb_mean"] >= 150_934
        and result["D5"]["formal_process_memory"]["handles_mean"] >= 900
        and result["D5"]["formal_process_handle"]["handles_mean"] >= 1451
        and result["D5"]["formal_process_handle"]["memory_kb_mean"] >= 95_674
        and result["D5"]["formal_process_handle"]["cpu_mean"] <= 0.235
        and all(row["process_metric_scale"]["memory_working_set_kb"] >= 150_934 for row in logical)
        and all(row["process_metric_scale"]["memory_handles"] >= 900 for row in logical)
        and all(row["process_metric_scale"]["handle_handles"] >= 1451 for row in logical)
        and all(row["process_metric_scale"]["handle_memory_kb"] >= 95_674 for row in logical)
        and all(row["process_metric_scale"]["handle_cpu"] <= 0.235 for row in logical)
    ) else "FAIL"
    result["D6"]["status"] = "PASS" if (
        result["D6"]["formal_total_mean"] >= 29_157
        and result["D6"]["formal_read_mean"] >= 14_013
        and result["D6"]["formal_max_total_component_error"] <= 1.0
        and all(row["process_metric_scale"]["disk_total"] >= 29_157 for row in logical)
        and all(row["process_metric_scale"]["disk_read"] >= 14_013 for row in logical)
    ) else "FAIL"
    result["D7"]["status"] = "PASS" if (
        2 <= result["D7"]["formal_unique_leaders"] <= 10
        and 2 <= result["D7"]["formal_leader_changes"] <= 21
        and all(2 <= row["rankings"]["process"]["unique_leaders"] <= 10 for row in logical)
        and all(2 <= row["rankings"]["process"]["leader_changes"] <= 21 for row in logical)
    ) else "FAIL"
    result["total_remaining_must_fix"] = sum(result[key]["status"] != "PASS" for key in (f"D{i}" for i in range(1, 10)))
    result["total_unresolved_needs_more_evidence"] = 2
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--compare", type=Path, required=True)
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.messages, args.summary, args.matrix, args.compare, args.package_root)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = "\n".join(
        f"- D{index}: {result[f'D{index}']['status']}"
        for index in range(1, 10)
    )
    markdown = f"""# FINAL FIX BATCH regression report\n\n- Formal seed: {result['formal_seed']}\n- Messages SHA256: `{result['formal_messages_sha256']}`\n- Remaining must-fix: {result['total_remaining_must_fix']}\n- Needs more evidence: {result['total_unresolved_needs_more_evidence']}\n\n{rows}\n\nAll statistics in the JSON companion are generated directly from the packaged formal `messages.jsonl`, its `summary.json`, the final ten-seed matrix, and the compare-tool output.\n"""
    args.output_md.write_text(markdown, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
