from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fidelity_audit import FidelityError, load_jsonl, validate_envelope
from telemetry.local_env import load_local_environment


ROOT = Path(__file__).resolve().parent
DEFAULT_CONTRACT = ROOT / "fidelity_contract.json"
DEFAULT_LOCAL_ENV = ROOT / "local_env.json"


def validate_windows_envelope(
    envelope: dict[str, Any],
    *,
    contract: dict[str, Any],
    expected_environment: dict[str, str],
) -> None:
    if envelope.get("provider") != "windows-validation":
        raise FidelityError(f"unexpected provider: {envelope.get('provider')!r}")
    metrics = envelope.get("metrics")
    if not isinstance(metrics, dict):
        raise FidelityError("metrics must be an object")
    actual = metrics.get("local_environment")
    if actual != expected_environment:
        raise FidelityError("local_environment does not exactly match local_env.json")

    metadata = envelope.get("metadata")
    if not isinstance(metadata, dict):
        raise FidelityError("metadata must be an object")
    if metadata.get("environment_source") != "declared-current-windows-machine":
        raise FidelityError("environment_source marker missing or incorrect")

    # Reuse the complete hybrid host-isolation contract for the dynamic portion.
    # Only the explicit local_environment baseline is removed from this derived
    # view; all CPU/memory/disk/process/network restrictions still apply.
    derived = json.loads(json.dumps(envelope))
    derived["provider"] = "hybrid-synthetic-network"
    derived["metrics"].pop("local_environment", None)
    validate_envelope(derived, contract)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Windows current-machine runtime output")
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--local-env", type=Path, default=DEFAULT_LOCAL_ENV)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()

    expected = load_local_environment(args.local_env)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    rows = load_jsonl(args.jsonl)
    for row in rows:
        validate_windows_envelope(row, contract=contract, expected_environment=expected)
    print(f"windows validation audit: OK ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
