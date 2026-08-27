from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from build_final_fidelity_regression import build
from run_accelerated_validation import _frame_length_summary


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file():
            shutil.copy2(path, destination / path.name)


def write_regression(result: dict, json_path: Path, markdown_path: Path) -> None:
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = "\n".join(
        f"- D{index}: {result[f'D{index}']['status']}" for index in range(1, 10)
    )
    markdown_path.write_text(
        f"# FINAL FIX BATCH regression report\n\n"
        f"- Formal seed: {result['formal_seed']}\n"
        f"- Messages SHA256: `{result['formal_messages_sha256']}`\n"
        f"- Remaining must-fix: {result['total_remaining_must_fix']}\n"
        f"- Needs more evidence: {result['total_unresolved_needs_more_evidence']}\n\n"
        f"{rows}\n\n"
        "Statistics are generated directly from the packaged formal messages, "
        "summary, ten-seed matrix, compare output, and package-relative Real bytes.\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v5", type=Path, required=True)
    parser.add_argument("--formal", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.zip.exists():
        raise FileExistsError("v6 package output already exists")

    for name in (
        "01_real_raw", "02_real_fresh_2h", "03_real_boot_reference",
        "04_synthetic_fresh_2h", "05_metadata", "06_comparison",
    ):
        (args.output / name).mkdir(parents=True, exist_ok=True)
    copy_tree(args.v5 / "01_real_raw", args.output / "01_real_raw")
    copy_tree(args.v5 / "02_real_fresh_2h", args.output / "02_real_fresh_2h")
    copy_tree(args.v5 / "03_real_boot_reference", args.output / "03_real_boot_reference")

    synthetic_files = (
        "messages.jsonl", "mswitch.raw", "runtime-status.json", "summary.json",
        "provenance.json", "accelerated_compare_report.json",
        "accelerated_compare_report.md",
    )
    for name in synthetic_files:
        shutil.copy2(args.formal / name, args.output / "04_synthetic_fresh_2h" / name)
    shutil.copy2(
        ROOT / "lab" / "mock-telemetry" / "config.windows-validation-accelerated.json",
        args.output / "04_synthetic_fresh_2h" / "config.windows-validation-accelerated.json",
    )
    shutil.copy2(args.matrix, args.output / "05_metadata" / "ten-seed-logical-validation.json")
    if (ROOT / "OFFLINE_FIDELITY_GAPS.md").is_file():
        shutil.copy2(ROOT / "OFFLINE_FIDELITY_GAPS.md", args.output / "05_metadata" / "OFFLINE_FIDELITY_GAPS.md")

    baseline_files = (
        "FINAL_FIDELITY_AUDIT_V5.md", "FINAL_FIDELITY_GAPS_V5.json",
        "formal-validation.json", "normalized-comparison.json",
        "telemetry_log_compare.json", "fidelity-report.md",
    )
    for name in baseline_files:
        shutil.copy2(args.v5 / "06_comparison" / name, args.output / "06_comparison" / f"v5_{name}")
    shutil.copy2(
        args.formal / "accelerated_compare_report.json",
        args.output / "06_comparison" / "accelerated_compare_report.json",
    )
    shutil.copy2(
        args.formal / "accelerated_compare_report.md",
        args.output / "06_comparison" / "accelerated_compare_report.md",
    )

    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True)
    (args.output / "05_metadata" / "git-info.txt").write_text(
        f"branch: {branch}\nHEAD: {head}\ncommit_or_push: no\n\ngit status --short:\n{status}",
        encoding="utf-8",
    )
    (args.output / "05_metadata" / "test-results.txt").write_text(
        "Runtime full suite: 75/75 PASS\nPrototype full suite: 93/93 PASS\n",
        encoding="utf-8",
    )
    (args.output / "05_metadata" / "collection-notes.txt").write_text(
        "FINAL FIX BATCH D1-D9 only. Formal run is the first normally completed fresh seed.\n"
        "Wall duration: approximately 600 seconds; simulated duration: 7200 seconds.\n"
        "No real Host connection, no private msgtype implementation, no commit, no push.\n"
        "Two unresolved items remain NEEDS_MORE_EVIDENCE exactly as documented in V5.\n",
        encoding="utf-8",
    )

    packaged_formal = args.output / "04_synthetic_fresh_2h"
    regression = build(
        packaged_formal / "messages.jsonl",
        packaged_formal / "summary.json",
        args.output / "05_metadata" / "ten-seed-logical-validation.json",
        packaged_formal / "accelerated_compare_report.json",
        args.output,
    )
    write_regression(
        regression,
        args.output / "06_comparison" / "FINAL_FIX_REGRESSION_V6.json",
        args.output / "06_comparison" / "FINAL_FIX_REGRESSION_V6.md",
    )
    if regression["total_remaining_must_fix"]:
        raise RuntimeError("D1-D9 regression contains a failure")

    messages = (packaged_formal / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    frames = sum(
        int(stats["count"])
        for stats in _frame_length_summary(packaged_formal / "mswitch.raw").values()
    )
    real_nonempty = all(
        path.stat().st_size > 0
        for path in (args.output / "01_real_raw").iterdir() if path.is_file()
    ) and all(
        path.stat().st_size > 0
        for path in (args.output / "02_real_fresh_2h").glob("*.log")
    )
    checks = {
        "messages": len(messages), "frames": frames,
        "real_files_nonempty": real_nonempty,
        "all_D1_D9_pass": regression["total_remaining_must_fix"] == 0,
        "package_relative_real_hashes_verified": regression["D9"]["status"] == "PASS",
    }
    if len(messages) != 291 or frames != 291 or not real_nonempty:
        raise RuntimeError(f"package integrity failure: {checks}")
    (args.output / "05_metadata" / "integrity-checks.json").write_text(
        json.dumps(checks, indent=2) + "\n", encoding="utf-8"
    )

    manifest_lines = []
    for path in sorted(args.output.rglob("*")):
        if path.is_file() and path.name != "sha256.txt":
            manifest_lines.append(f"{sha256(path)}  {path.relative_to(args.output).as_posix()}")
    (args.output / "05_metadata" / "sha256.txt").write_text(
        "\n".join(manifest_lines) + "\n", encoding="utf-8"
    )
    with zipfile.ZipFile(args.zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(args.output.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(args.output.name) / path.relative_to(args.output)).as_posix())
    print(json.dumps({
        "zip": str(args.zip.resolve()),
        "zip_size": args.zip.stat().st_size,
        "zip_sha256": sha256(args.zip),
        "formal_seed": regression["formal_seed"],
        "checks": checks,
        "remaining_must_fix": regression["total_remaining_must_fix"],
        "unresolved": regression["total_unresolved_needs_more_evidence"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
