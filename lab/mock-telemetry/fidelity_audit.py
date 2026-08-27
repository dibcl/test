from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable


class FidelityError(AssertionError):
    pass


def _require_keys(container: dict[str, Any], required: Iterable[str], label: str) -> None:
    missing = [key for key in required if key not in container]
    if missing:
        raise FidelityError(f"{label} missing keys: {missing}")


def _number(value: Any, label: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FidelityError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise FidelityError(f"{label} must be finite")
    return result


def _range(value: Any, bounds: list[float], label: str, *, allow_none: bool = False) -> None:
    number = _number(value, label, allow_none=allow_none)
    if number is None:
        return
    low, high = (float(bounds[0]), float(bounds[1]))
    if not low <= number <= high:
        raise FidelityError(f"{label} outside [{low}, {high}]: {number}")


def _walk(value: Any, path: str = "metrics"):
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            yield key, item, child
            yield from _walk(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def validate_envelope(envelope: dict[str, Any], contract: dict[str, Any]) -> None:
    expected_provider = contract.get("provider", "hybrid-synthetic-network")
    if envelope.get("provider") != expected_provider:
        raise FidelityError(f"unexpected provider: {envelope.get('provider')!r}")

    metrics = envelope.get("metrics")
    if not isinstance(metrics, dict):
        raise FidelityError("metrics must be an object")

    _require_keys(metrics, contract["required_metric_keys"], "metrics")
    for key in contract["forbidden_metric_keys"]:
        if key in metrics:
            raise FidelityError(f"forbidden host-derived metric present: {key}")

    forbidden_recursive = set(contract.get("forbidden_recursive_keys", []))
    forbidden_markers = tuple(str(item).lower() for item in contract.get("forbidden_host_markers", []))
    for key, value, path in _walk(metrics):
        if key.lower() in forbidden_recursive:
            raise FidelityError(f"forbidden host-derived key present at {path}")
        if isinstance(value, str):
            lowered = value.lower()
            for marker in forbidden_markers:
                if marker and marker in lowered:
                    raise FidelityError(f"forbidden host marker {marker!r} present at {path}")

    cpu = metrics["cpu"]
    memory = metrics["memory"]
    disk = metrics["disk_io"]
    network = metrics["network_io"]
    processes = metrics["process_snapshot"]
    if not all(isinstance(item, dict) for item in (cpu, memory, disk, network, processes)):
        raise FidelityError("metric sections must be objects")

    _require_keys(cpu, contract["required_cpu_keys"], "cpu")
    _require_keys(memory, contract["required_memory_keys"], "memory")
    _require_keys(disk, contract["required_disk_keys"], "disk_io")
    _require_keys(network, contract["required_network_keys"], "network_io")
    _require_keys(processes, contract["required_process_keys"], "process_snapshot")

    for key in contract["forbidden_network_keys"]:
        if key in network:
            raise FidelityError(f"forbidden network identity/state key present: {key}")

    expected_sources = contract.get("expected_sources", {})
    for section, expected in expected_sources.items():
        actual = metrics[section].get("source")
        if actual != expected:
            raise FidelityError(f"{section}.source expected {expected!r}, got {actual!r}")

    ranges = contract.get("numeric_ranges", {})
    _range(cpu["percent"], ranges["cpu_percent"], "cpu.percent")
    per_core = cpu.get("per_core")
    if not isinstance(per_core, list) or len(per_core) != int(contract["cpu_core_count"]):
        raise FidelityError(f"cpu.per_core must contain exactly {contract['cpu_core_count']} values")
    for index, value in enumerate(per_core):
        _range(value, ranges["cpu_percent"], f"cpu.per_core[{index}]")

    _range(memory["percent"], ranges["memory_percent"], "memory.percent")
    _range(memory["paged_pool_mb"], ranges["pool_mb"], "memory.paged_pool_mb")
    _range(memory["nonpaged_pool_mb"], ranges["pool_mb"], "memory.nonpaged_pool_mb")

    _range(disk["activity_rate"], ranges["disk_activity_rate"], "disk_io.activity_rate")
    per_disk = disk.get("per_disk")
    if not isinstance(per_disk, list):
        raise FidelityError("disk_io.per_disk must be a list")
    expected_disks = list(contract.get("disk_names", []))
    actual_disks = [str(item.get("name")) for item in per_disk if isinstance(item, dict)]
    if actual_disks != expected_disks:
        raise FidelityError(f"disk names expected {expected_disks}, got {actual_disks}")
    for index, item in enumerate(per_disk):
        if not isinstance(item, dict):
            raise FidelityError(f"disk_io.per_disk[{index}] must be an object")
        for required in ("name", "size_gb", "used_percent", "activity_rate"):
            if required not in item:
                raise FidelityError(f"disk_io.per_disk[{index}] missing {required}")
        _range(item["used_percent"], [0.0, 100.0], f"disk_io.per_disk[{index}].used_percent")
        _range(item["activity_rate"], ranges["disk_activity_rate"], f"disk_io.per_disk[{index}].activity_rate")

    for key in ("tx_bytes_per_second", "rx_bytes_per_second"):
        _range(network[key], ranges["network_rate"], f"network_io.{key}", allow_none=True)
    if network.get("scope") != "aggregate-rate-only":
        raise FidelityError(f"unexpected network scope: {network.get('scope')!r}")

    allowed_names = set(contract.get("allowed_process_names", []))
    required_row_keys = contract["required_process_row_keys"]
    max_rows = int(contract.get("max_process_rows", 10))
    group_names = ("process", "process_memory", "process_handle", "process_diskio", "process_netio")
    for group in group_names:
        rows = processes.get(group)
        if not isinstance(rows, list) or not rows:
            raise FidelityError(f"process_snapshot.{group} must be a non-empty list")
        if len(rows) > max_rows:
            raise FidelityError(f"process_snapshot.{group} exceeds {max_rows} rows")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise FidelityError(f"process_snapshot.{group}[{index}] must be an object")
            _require_keys(row, required_row_keys, f"process_snapshot.{group}[{index}]")
            if row["name"] not in allowed_names:
                raise FidelityError(f"undeclared process name: {row['name']!r}")
            if isinstance(row["pid"], bool) or not isinstance(row["pid"], int) or row["pid"] <= 0:
                raise FidelityError(f"invalid pid in process_snapshot.{group}[{index}]")
            _range(row["cpu_percent"], ranges["cpu_percent"], f"{group}[{index}].cpu_percent")
            for field in ("rss_mb", "handles", "threads", "disk_io_rate", "network_io_rate"):
                value = _number(row[field], f"{group}[{index}].{field}")
                if value is not None and value < 0:
                    raise FidelityError(f"{group}[{index}].{field} must be non-negative")
    if processes.get("keyprocess") not in allowed_names:
        raise FidelityError(f"undeclared keyprocess: {processes.get('keyprocess')!r}")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise FidelityError(f"no JSONL rows in {path}")
    result = []
    for index, row in enumerate(rows):
        value = json.loads(row)
        if not isinstance(value, dict):
            raise FidelityError(f"JSONL row {index} must be an object")
        result.append(value)
    return result


def validate_sequence(rows: list[dict[str, Any]], contract: dict[str, Any]) -> None:
    for row in rows:
        validate_envelope(row, contract)
    if len(rows) >= 2:
        cpu_values = [row["metrics"]["cpu"]["percent"] for row in rows]
        memory_values = [row["metrics"]["memory"]["percent"] for row in rows]
        disk_values = [row["metrics"]["disk_io"]["activity_rate"] for row in rows]
        if len(set(cpu_values)) == len(set(memory_values)) == len(set(disk_values)) == 1:
            raise FidelityError("all synthetic system signals are frozen across the sequence")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generic host-isolated runtime fidelity")
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name("fidelity_contract.json"))
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    rows = load_jsonl(args.jsonl)
    validate_sequence(rows, contract)
    print(f"runtime fidelity audit: OK ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
