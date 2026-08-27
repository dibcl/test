from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLASS_A = {8007, 8059, 9053, 9055, 9056}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _frame_count(path: Path) -> int:
    count = 0
    escaped = False
    current = 0
    for value in path.read_bytes():
        current += 1
        if escaped:
            escaped = False
        elif value == 0x5C:
            escaped = True
        elif value == 0x3B:
            count += 1
            current = 0
    if escaped or current:
        raise ValueError("mswitch.raw contains a truncated frame")
    return count


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], text=True, encoding="utf-8"
    ).strip()


def build(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    archive = args.zip.resolve()
    if output.exists() or archive.exists():
        raise FileExistsError("blind package output already exists; refusing to overwrite")
    output.mkdir(parents=True)

    real_raw = output / "01_real_raw"
    extracted = output / "02_class_a_real_evidence"
    synthetic = output / "03_synthetic_fresh_2h"
    validation = output / "04_validation"
    provenance = output / "05_provenance"
    for directory in (real_raw, extracted, synthetic, validation, provenance):
        directory.mkdir(parents=True)

    for name in (
        "vmswitch.log", "vmbooster.log", "vmbooster_bak.log",
        "QoEAgent.log", "QoEAgent_bak.log",
    ):
        _copy(args.audit_root / "evidence" / "logs" / name, real_raw / name)

    event_source = args.audit_root / "analysis" / "serial_events.jsonl"
    class_events = []
    selected_lines: set[int] = set()
    with event_source.open("r", encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event["direction"] == "Guest->Host" and int(event["msgtype"]) in CLASS_A:
                class_events.append((line, event))
                selected_lines.add(int(event["source_line"]))
    with (extracted / "class-a-serial-events.jsonl").open("w", encoding="utf-8", newline="") as handle:
        for line, _ in class_events:
            handle.write(line)

    raw_lines = (args.audit_root / "evidence" / "logs" / "vmswitch.log").read_bytes().splitlines(keepends=True)
    with (extracted / "vmswitch-class-a-source-lines.log").open("wb") as handle:
        for line_number in sorted(selected_lines):
            handle.write(raw_lines[line_number - 1])
    _copy(
        ROOT / "lab" / "mock-telemetry" / "fixtures" / "class-a-observed-baseline.json",
        extracted / "class-a-observed-baseline.json",
    )
    for name in (
        "FULL_GUEST_PROTOCOL_EVIDENCE_AUDIT.md",
        "FULL_GUEST_PROTOCOL_MATRIX.json",
        "MESSAGE_RELATION_GRAPH.json",
    ):
        _copy(args.audit_root / "reports" / name, extracted / name)

    formal_files = (
        "messages.jsonl", "mswitch.raw", "runtime-status.json", "summary.json",
        "provenance.json", "accelerated_compare_report.json",
        "accelerated_compare_report.md", "class_a_compare_report.json",
        "class_a_compare_report.md", "six_message_regression.json",
    )
    for name in formal_files:
        _copy(args.formal / name, synthetic / name)
    _copy(
        ROOT / "lab" / "mock-telemetry" / "config.windows-validation-accelerated.json",
        synthetic / "config.windows-validation-accelerated.json",
    )

    _copy(args.logical, validation / "class-a-10-seed-logical-validation.json")
    _copy(args.formal / "class_a_compare_report.json", validation / "class_a_compare_report.json")
    _copy(args.formal / "class_a_compare_report.md", validation / "class_a_compare_report.md")
    _copy(args.formal / "six_message_regression.json", validation / "six_message_regression.json")
    (validation / "test-results.txt").write_text(
        f"Runtime: {args.runtime_result}\nPrototype: {args.prototype_result}\n",
        encoding="utf-8",
    )

    messages = (synthetic / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    frames = _frame_count(synthetic / "mswitch.raw")
    summary = json.loads((synthetic / "summary.json").read_text(encoding="utf-8"))
    if len(messages) != int(summary["message_count"]) or frames != len(messages):
        raise ValueError("message/frame/summary counts do not match")
    if not summary["class_a"]["assessment"]["all_pass"]:
        raise ValueError("formal Class A assessment failed")
    if not summary["six_message_regression"]["pass"]:
        raise ValueError("formal six-message regression failed")

    matrix = json.loads(
        (args.audit_root / "reports" / "FULL_GUEST_PROTOCOL_MATRIX.json").read_text(
            encoding="utf-8"
        )
    )
    frozen_count = int(matrix["coverage"]["six_message_guest_to_host_total"])
    class_a_count = sum(
        int(item["total_count"])
        for item in matrix["messages"]
        if item["direction"] == "Guest->Host" and item["offline_class"] == "A"
    )
    guest_total = int(matrix["coverage"]["guest_to_host_total"])
    coverage = {
        "observable_guest_to_host_total": guest_total,
        "frozen_six_observed_count": frozen_count,
        "class_a_observed_count": class_a_count,
        "covered_observed_count": frozen_count + class_a_count,
        "covered_observed_share": (frozen_count + class_a_count) / guest_total,
        "remaining_observed_count": guest_total - frozen_count - class_a_count,
        "remaining_extra_msgtype_classes": {"B": 6, "C": 2, "D": 12},
        "scope_note": "Coverage is count-weighted Guest->Host serial observability over the 90.8h audit, not semantic completeness.",
    }
    (provenance / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    git_info = {
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "HEAD"),
        "upstream_head": _git("rev-parse", "@{upstream}"),
        "status_porcelain": _git("status", "--porcelain=v1"),
        "note": "Working tree intentionally contains uncommitted TARGET_B_CLASS_A_OFFLINE_MODEL changes; no commit/push was performed.",
    }
    (provenance / "git-info.json").write_text(
        json.dumps(git_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    collection = {
        "real_observation_start": matrix["observation_start"],
        "real_observation_end": matrix["observation_end"],
        "real_coverage_hours": matrix["coverage_hours"],
        "class_a_real_event_count": len(class_events),
        "synthetic_effective_seed": summary["run_seed"],
        "synthetic_real_elapsed_seconds": summary["real_elapsed_seconds"],
        "synthetic_simulated_duration_seconds": summary["simulated_duration_seconds"],
        "synthetic_message_count": len(messages),
        "synthetic_frame_count": frames,
        "synthetic_message_counts": summary["message_counts"],
        "offline_only": True,
    }
    (provenance / "collection.json").write_text(
        json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    manifest = []
    for path in sorted(item for item in output.rglob("*") if item.is_file()):
        manifest.append(f"{_sha256(path)}  {path.relative_to(output).as_posix()}")
    (provenance / "SHA256.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")

    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for path in sorted(item for item in output.rglob("*") if item.is_file()):
            handle.write(path, (Path(output.name) / path.relative_to(output)).as_posix())
    return {
        "output": str(output), "zip": str(archive), "zip_sha256": _sha256(archive),
        "zip_size": archive.stat().st_size, "messages": len(messages), "frames": frames,
        "seed": summary["run_seed"], "coverage": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--formal", type=Path, required=True)
    parser.add_argument("--logical", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--runtime-result", required=True)
    parser.add_argument("--prototype-result", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
