"""Run the frozen Mock Telemetry Agent from a JSON configuration."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os

from mock_telemetry_agent import FaultPlan, FrozenProfile, InMemoryTransport, MockTelemetryAgent, build_transport
from mock_guest_session import BidirectionalGuestSession
from profile_loader import load_rendered_profile
from real_device_profile import RealDeviceProfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.path.join(ROOT, "lab", "mock-telemetry", "config.memory.json"))
    args = parser.parse_args()
    with open(args.config, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    variables = dict(config.get("profile_variables", {}))
    local_env = variables.get("local_env")
    rendered = load_rendered_profile(
        _resolve(config["profile"]),
        local_env_path=_resolve(local_env) if local_env else None,
    )
    profile = FrozenProfile.from_dict(rendered)
    external_profile_path = config.get("real_device_profile")
    if external_profile_path:
        profile = RealDeviceProfile.load(_resolve(external_profile_path)).apply_to_frozen(profile)
    transport = build_transport(config["transport"])
    session = None
    if config.get("bidirectional", False):
        session = BidirectionalGuestSession(transport, profile.identity, profile.environment)
    agent = MockTelemetryAgent(
        profile,
        transport,
        faults=FaultPlan.from_dict(config.get("faults")),
        control_session=session,
    )
    try:
        agent.start()
        agent.run_for(int(config.get("duration_seconds", 600)))
    finally:
        agent.close()
    result = {
        "transport": config["transport"]["type"],
        "sent_counts": {str(key): value for key, value in sorted(agent.sent_counts.items())},
    }
    if isinstance(transport, InMemoryTransport):
        result["captured_by_test_api"] = len(transport.messages)
        result["captured_ids"] = dict(sorted(Counter(item.int_msgid for item in transport.messages).items()))
    if session is not None:
        result["session"] = {
            "state": session.state.name,
            "heartbeat_sequence": session.heartbeat_sequence,
            "acks": vars(session.acks),
        }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
