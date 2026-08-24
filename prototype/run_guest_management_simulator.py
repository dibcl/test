"""Run one complete offline Guest/fake-Host exchange using the current log."""

from __future__ import annotations

import argparse
import json

from guest_management_simulator import FakeHost, OfflineGuestSimulator, load_current_baseline


DEFAULT_LOG = r"C:\Program Files (x86)\vmtool\vm_booster\vmswitch.log"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--heartbeats", type=int, default=3)
    args = parser.parse_args()
    if args.heartbeats < 1 or args.heartbeats > 1000:
        parser.error("--heartbeats must be between 1 and 1000")

    baseline = load_current_baseline(args.log)
    host = FakeHost(baseline.offline_uuid)
    guest = OfflineGuestSimulator(baseline, host)
    guest.run_startup()
    for _ in range(args.heartbeats):
        guest.heartbeat()
    guest.request_ip_info(521910635)
    result = {
        "state": guest.state.name,
        "heartbeats": guest.heartbeat_count,
        "guest_messages_seen_by_fake_host": len(host.received),
        "baseline": baseline.public_summary(),
        "transport": "in-memory-only",
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

