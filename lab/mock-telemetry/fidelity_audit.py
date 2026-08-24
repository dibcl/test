from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class FidelityError(AssertionError):
    pass


def _require_keys(container: dict[str, Any], required: list[str], label: str) -> None:
    missing = [key for key in required if key not in container]
    if missing:
        raise FidelityError(f"{label} missing keys: {missing}")


def validate_envelope(envelope: dict[str, Any], contract: dict[str, Any]) -> None:
    if envelope.get("provider") != "hybrid-synthetic-network":
        raise FidelityError(f"unexpected provider: {envelope.get('provider')!r}")

    metrics = envelope.get("metrics")
    if not isinstance(metrics, dict):
        raise FidelityError("metrics must be an object")

    _require_keys(metrics, contract["required_metric_keys"], "metrics")
    for key in contract["forbidden_metric_keys"]:
        if key in metrics:
            raise FidelityError(f"forbidden host-derived metric present: {key}")

    cpu = metrics["cpu"]
    memory = metrics["memory"]
    disk = metrics["disk_io"]
    network = metrics["network_io"]
    processes = metrics["process_snapshot"]

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

    per_core = cpu.get("per_core")
    if not isinstance(per_core, list) or not per_core:
        raise FidelityError("cpu.per_core must be a non-empty list")

    per_disk = disk.get("per_disk")
    if not isinstance(per_disk, list) or not per_disk:
        raise FidelityError("disk_io.per_disk must be a non-empty list")

    for key in ("process", "process_memory", "process_handle", "process_diskio", "process_netio"):
        value = processes.get(key)
        if not isinstance(value, list) or not value:
            raise FidelityError(f"process_snapshot.{key} must be a non-empty list")


def load_last_jsonl(path: Path) -> dict[str, Any]:
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise FidelityError(f"no JSONL rows in {path}")
    value = json.loads(rows[-1])
    if not isinstance(value, dict):
        raise FidelityError("last JSONL row must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generic hybrid-runtime fidelity")
    parser.add_argument("jsonl", type=Path)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path(__file__).with_name("fidelity_contract.json"),
    )
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    envelope = load_last_jsonl(args.jsonl)
    validate_envelope(envelope, contract)
    print("runtime fidelity audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
