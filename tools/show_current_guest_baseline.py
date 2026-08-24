"""Print a redacted summary of the latest local Guest-management baseline."""

from __future__ import annotations

import argparse
import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "prototype"))

from guest_management_simulator import load_current_baseline  # noqa: E402


DEFAULT_LOG = r"C:\Program Files (x86)\vmtool\vm_booster\vmswitch.log"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=DEFAULT_LOG)
    args = parser.parse_args()
    baseline = load_current_baseline(args.log)
    # ASCII escaping keeps URL-decoded non-ASCII fields lossless even when the
    # Windows console is using a legacy code page.
    print(json.dumps(baseline.public_summary(), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
