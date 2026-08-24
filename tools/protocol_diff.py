"""Read-only Protocol Diff Matrix generator.

Compares observed Guest->Host messages in vmswitch.log with messages emitted by
the loopback/memory Mock Telemetry Agent.  It reports IDs, JSON field types and
cadence only; values such as UUID, MAC, IP and software names are not printed.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
import re
import statistics
import sys
from typing import Any


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOTYPE = os.path.join(ROOT, "prototype")
sys.path.insert(0, PROTOTYPE)

from mock_guest_session import BidirectionalGuestSession, TestHostResponder  # noqa: E402
from mock_telemetry_agent import (  # noqa: E402
    FaultPlan,
    FrozenProfile,
    InMemoryTransport,
    MockTelemetryAgent,
)


REAL_SEND = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) .*?"
    r"WriteSerialPort succ.*?int_msgid=(?P<id>-?\d+), "
    r"dst_mod=(?P<dst>-?\d+), data_len=(?P<len>\d+),msg=(?P<payload>.*)$"
)
REAL_HOST = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) .*?"
    r"send_msg dst_type=(?P<dtype>-?\d+), int_msgid=(?P<id>-?\d+), "
    r"dst_mod=(?P<dst>-?\d+), data_len=(?P<len>\d+), data=(?P<payload>.*)$"
)


NAMES = {
    1300: "MAC report",
    4002: "heartbeat",
    4004: "component versions",
    4100: "heartbeat ACK",
    7002: "admin upgrade request (historical)",
    8007: "RDP status",
    8008: "VM info request",
    8009: "VM info response",
    8047: "local event forwarded",
    8052: "OAS/report policy (Host control)",
    8059: "gateway/IP alarm",
    8060: "lock state",
    8063: "CSAP endpoint request",
    8064: "CSAP endpoint response",
    9011: "IP info request",
    9012: "IP info response",
    9050: "environment",
    9051: "performance",
    9052: "process Top",
    9053: "activity/session",
    9054: "software/KB",
    9055: "VM start time",
    9056: "ICE connectivity",
    9060: "ICE trace",
    9502: "QoE OAS interval (Host control)",
    0x8102BF: "network report",
    0x8102C1: "network ACK",
    0x8102C5: "OS report",
    0x8102C7: "OS ACK",
}

# A matching ID alone is not evidence that an unknown production payload
# semantic has been reproduced. Keep these explicit in generated reports.
SYNTHETIC_SHAPE_ONLY = {8047, 8060, 0x8102C4}


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def schema(value: Any, prefix: str = "$") -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)

    def visit(item: Any, path: str) -> None:
        result[path].add(type_name(item))
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{path}.{key}")
        elif isinstance(item, list):
            for child in item[:20]:
                visit(child, f"{path}[]")

    visit(value, prefix)
    return dict(result)


def parse_json_payload(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text.startswith("{") and "{" in text:
        text = text[text.index("{"):]
    text = re.sub(r",\s*}$", "}", text)
    if not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def normalize_host_msgid(raw_msgid: int, payload_text: str) -> int:
    """Map int_msgid=0 payload-dispatched Host messages to their business ID."""
    if raw_msgid != 0:
        return raw_msgid
    match = re.search(r"msgtype[=:][\"']?(\d+)", payload_text)
    return int(match.group(1)) if match else raw_msgid


@dataclass
class Observation:
    counts: Counter[int] = field(default_factory=Counter)
    times: dict[int, list[datetime]] = field(default_factory=lambda: defaultdict(list))
    schemas: dict[int, dict[str, set[str]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))

    def add(self, msgid: int, timestamp: datetime, payload: dict[str, Any] | None) -> None:
        self.counts[msgid] += 1
        self.times[msgid].append(timestamp)
        if payload is not None:
            for path, types in schema(payload).items():
                self.schemas[msgid][path].update(types)


def parse_real(path: str, since: datetime | None) -> tuple[Observation, Observation]:
    guest = Observation()
    host = Observation()
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = REAL_SEND.match(line)
            target = guest
            if match is None:
                match = REAL_HOST.match(line)
                target = host
            if match is None:
                continue
            timestamp = datetime.strptime(match.group("time"), "%Y-%m-%d %H:%M:%S")
            if since is not None and timestamp < since:
                continue
            payload_text = match.group("payload")
            msgid = int(match.group("id")) & 0xFFFFFFFF
            if target is host:
                msgid = normalize_host_msgid(msgid, payload_text)
            target.add(msgid, timestamp, parse_json_payload(payload_text))
    return guest, host


def run_mock(profile_path: str, duration: int) -> tuple[Observation, Observation]:
    profile = FrozenProfile.load(profile_path)
    transport = InMemoryTransport(responder=TestHostResponder())
    session = BidirectionalGuestSession(transport, profile.identity, profile.environment)
    agent = MockTelemetryAgent(profile, transport, faults=FaultPlan(), control_session=session)
    agent.start()
    session.set_lock_state(agent._stamp(), True)
    session.forward_test_local_event(agent._stamp(), "synthetic-protocol-diff-event")
    agent.run_for(duration)
    agent.close()
    guest = Observation()
    host = Observation()
    for envelope in transport.messages:
        timestamp = datetime.fromisoformat(envelope.emitted_at)
        target = host if envelope.source_module == 0x80000000 else guest
        target.add(envelope.int_msgid, timestamp, envelope.payload)
    return guest, host


def cadence(values: list[datetime]) -> str:
    if len(values) < 2:
        return "n/a"
    deltas = [(right - left).total_seconds() for left, right in zip(values, values[1:])]
    return f"median={statistics.median(deltas):.2f}s, min={min(deltas):.2f}s, max={max(deltas):.2f}s"


def label(msgid: int) -> str:
    suffix = f" ({NAMES[msgid]})" if msgid in NAMES else ""
    return f"{msgid} / 0x{msgid:08x}{suffix}"


def render_schema_diff(msgid: int, real: Observation, mock: Observation) -> list[str]:
    real_schema = real.schemas.get(msgid, {})
    mock_schema = mock.schemas.get(msgid, {})
    paths = sorted(set(real_schema) | set(mock_schema))
    lines = [f"### {label(msgid)}", "", "| Field path | Windows types | Mock types | Result |", "|---|---|---|---|"]
    for path in paths:
        left = ", ".join(sorted(real_schema.get(path, set()))) or "—"
        right = ", ".join(sorted(mock_schema.get(path, set()))) or "—"
        if path not in real_schema:
            result = "Mock-only"
        elif path not in mock_schema:
            result = "Missing in Mock"
        elif real_schema[path] != mock_schema[path]:
            result = "Type mismatch"
        else:
            result = "Match"
        lines.append(f"| `{path}` | {left} | {right} | {result} |")
    lines.append("")
    return lines


def render(real_guest: Observation, real_host: Observation, mock_guest: Observation, mock_host: Observation) -> str:
    lines = [
        "# Protocol Diff Matrix (generated)",
        "",
        "> Values are redacted. This output compares message IDs, JSON paths, data types and cadence only.",
        "",
        "## Guest → Host coverage",
        "",
        "| Protocol ID | Windows count | Mock count | Coverage | Windows cadence | Mock cadence |",
        "|---|---:|---:|---|---|---|",
    ]
    for msgid in sorted(set(real_guest.counts) | set(mock_guest.counts)):
        rc = real_guest.counts[msgid]
        mc = mock_guest.counts[msgid]
        if rc and mc and msgid in SYNTHETIC_SHAPE_ONLY:
            coverage = "Synthetic shape only"
        else:
            coverage = "Implemented" if rc and mc else ("Missing" if rc else "Mock-only")
        lines.append(
            f"| {label(msgid)} | {rc} | {mc} | {coverage} | "
            f"{cadence(real_guest.times[msgid])} | {cadence(mock_guest.times[msgid])} |"
        )
    lines += ["", "## Host → Guest coverage", "", "| Protocol ID | Windows count | Mock count | Coverage |", "|---|---:|---:|---|"]
    for msgid in sorted(set(real_host.counts) | set(mock_host.counts)):
        rc = real_host.counts[msgid]
        mc = mock_host.counts[msgid]
        coverage = "Implemented" if rc and mc else ("Missing" if rc else "Mock-only")
        lines.append(f"| {label(msgid)} | {rc} | {mc} | {coverage} |")
    lines += ["", "## Focused JSON schema diff", ""]
    for msgid in (4002, 9050, 9054):
        lines.extend(render_schema_diff(msgid, real_guest, mock_guest))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--real-log",
        default=r"C:\Program Files (x86)\vmtool\vm_booster\vmswitch.log",
    )
    parser.add_argument(
        "--profile",
        default=os.path.join(ROOT, "lab", "mock-telemetry", "baseline.synthetic.json"),
    )
    parser.add_argument("--since", help="Windows log lower bound, e.g. 2026-08-24T00:24:00")
    parser.add_argument("--mock-duration", type=int, default=600)
    parser.add_argument("--output")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since) if args.since else None
    real_guest, real_host = parse_real(args.real_log, since)
    mock_guest, mock_host = run_mock(args.profile, args.mock_duration)
    output = render(real_guest, real_host, mock_guest, mock_host)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(output + "\n")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
