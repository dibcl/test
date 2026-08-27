from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENTS = (
    ROOT.parent.parent / "full-guest-protocol-evidence-audit"
    / "analysis" / "serial_events.jsonl"
)
DEFAULT_OUTPUT = (
    ROOT / "lab" / "mock-telemetry" / "fixtures"
    / "class-a-observed-baseline.json"
)
CLASS_A = (8007, 8059, 9053, 9055, 9056)


def _timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y/%m/%d %H:%M:%S")


def _stats(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "min": None, "mean": None, "median": None,
                "stddev": None, "p90": None, "p95": None, "max": None}

    def percentile(q: float) -> float:
        position = (len(ordered) - 1) * q
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "count": len(ordered),
        "min": min(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "stddev": statistics.pstdev(ordered) if len(ordered) > 1 else 0.0,
        "p90": percentile(0.90),
        "p95": percentile(0.95),
        "max": max(ordered),
    }


def _cadence(events: list[dict[str, Any]]) -> dict[str, Any]:
    stamps = [_timestamp(item["timestamp"]) for item in events]
    return _stats([(right - left).total_seconds() for left, right in zip(stamps, stamps[1:])])


def _transition(values: list[str]) -> dict[str, Any]:
    transitions = Counter(zip(values, values[1:]))
    by_source = Counter(left for left, _ in zip(values, values[1:]))
    matrix = {
        source: {
            target: count / by_source[source]
            for (left, target), count in sorted(transitions.items()) if left == source
        }
        for source in sorted(by_source)
    }
    run_lengths: list[int] = []
    if values:
        current, length = values[0], 1
        for value in values[1:]:
            if value == current:
                length += 1
            else:
                run_lengths.append(length)
                current, length = value, 1
        run_lengths.append(length)
    return {
        "matrix": matrix,
        "changes": sum(left != right for left, right in zip(values, values[1:])),
        "persistence_ratio": (
            sum(left == right for left, right in zip(values, values[1:]))
            / (len(values) - 1) if len(values) > 1 else 1.0
        ),
        "run_length_samples": _stats([float(value) for value in run_lengths]),
    }


def _kv(payload: str) -> dict[str, str]:
    return {
        key.strip(): value.strip()
        for key, value in re.findall(r"(?:^|[;,])\s*([^=;,]+)=([^;,]*)", payload)
    }


def _parse_9053(payload: str) -> dict[str, Any]:
    return json.loads(payload)


def _parse_9056_row(row: str) -> list[str]:
    return next(csv.reader([row], skipinitialspace=True, quotechar="'"))


def analyze(events_path: Path) -> dict[str, Any]:
    all_events = [
        json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = {
        msgtype: [
            item for item in all_events
            if item["direction"] == "Guest->Host" and int(item["msgtype"]) == msgtype
        ]
        for msgtype in CLASS_A
    }
    observation_start = min(_timestamp(item["timestamp"]) for item in all_events)
    observation_end = max(_timestamp(item["timestamp"]) for item in all_events)
    hours = (observation_end - observation_start).total_seconds() / 3600.0

    values_8007 = []
    for item in events[8007]:
        match = re.search(r"rdp:'([^']*)'", item["payload"])
        values_8007.append(match.group(1) if match else "UNKNOWN")

    parsed_8059 = [_kv(item["payload"]) for item in events[8059]]
    variants_8059 = [
        "populated" if any(key in row for key in ("gateway", "ip", "hostname"))
        else "minimal"
        for row in parsed_8059
    ]
    states_8059 = [
        f"{row.get('alarmtype', '')}:{row.get('alarmnum', '')}:"
        f"{int(any(row.get(key, '') for key in ('gateway', 'ip', 'hostname')))}"
        for row in parsed_8059
    ]

    parsed_9053 = [_parse_9053(item["payload"]) for item in events[9053]]
    batch_sizes: list[float] = []
    encoding_styles: Counter[str] = Counter()
    category_codes: Counter[str] = Counter()
    payload_time_lags: list[float] = []
    event_time_lags: list[float] = []
    empty_batches = 0
    for event, payload in zip(events[9053], parsed_9053):
        logs = payload.get("logdatas", [])
        batch_sizes.append(float(len(logs)))
        empty_batches += int(not logs)
        payload_time = datetime.fromisoformat(payload["time"])
        outer = _timestamp(event["timestamp"])
        payload_time_lags.append((payload_time - outer).total_seconds())
        for row in logs:
            raw = str(row.get("log", ""))
            encoded = bool(re.search(r"%[0-9A-Fa-f]{2}|\+", raw))
            encoding_styles["percent_plus" if encoded else "plain"] += 1
            decoded = unquote_plus(raw) if encoded else raw
            fields = decoded.split("|")
            if len(fields) >= 5:
                category_codes["|".join(fields[2:5])] += 1
            try:
                event_time = datetime.fromisoformat(fields[0])
            except (ValueError, IndexError):
                continue
            event_time_lags.append((payload_time - event_time).total_seconds())

    parsed_9055 = [_parse_9053(item["payload"]) for item in events[9055]]
    startup_9050 = [
        item for item in all_events
        if item["direction"] == "Guest->Host" and int(item["msgtype"]) == 9050
    ]
    startup_offsets = []
    for item in events[9055]:
        stamp = _timestamp(item["timestamp"])
        future = [
            (_timestamp(candidate["timestamp"]) - stamp).total_seconds()
            for candidate in startup_9050
            if 0 <= (_timestamp(candidate["timestamp"]) - stamp).total_seconds() <= 10
        ]
        if future:
            startup_offsets.append(min(future))

    parsed_9056 = [_parse_9053(item["payload"]) for item in events[9056]]
    states_9056: list[str] = []
    row_widths: Counter[int] = Counter()
    time_create_delta: list[float] = []
    payload_outer_lag: list[float] = []
    for event, payload in zip(events[9056], parsed_9056):
        rows = payload.get("datas", [])
        for row in rows:
            fields = _parse_9056_row(str(row.get("row", "")))
            row_widths[len(fields)] += 1
            if len(fields) >= 9:
                states_9056.append("|".join(fields[5:9]))
                time_create_delta.append(
                    (datetime.fromisoformat(fields[0]) - datetime.fromisoformat(fields[1])).total_seconds()
                )
                payload_outer_lag.append(
                    (datetime.fromisoformat(fields[0]) - _timestamp(event["timestamp"])).total_seconds()
                )

    result: dict[str, Any] = {
        "evidence": {
            "source": "FULL_GUEST_PROTOCOL_EVIDENCE_AUDIT/analysis/serial_events.jsonl",
            "source_sha256": hashlib.sha256(events_path.read_bytes()).hexdigest(),
            "observation_start": observation_start.isoformat(),
            "observation_end": observation_end.isoformat(),
            "coverage_hours": hours,
            "class_a_serial_count": sum(len(value) for value in events.values()),
        },
        "8007": {
            "count": len(events[8007]), "count_per_hour": len(events[8007]) / hours,
            "cadence_seconds": _cadence(events[8007]),
            "payload_length": _stats([float(item["data_len"]) for item in events[8007]]),
            "rdp_values": dict(Counter(values_8007)),
            "rdp_transition": _transition(values_8007),
            "observed_payload": "msgtype:'8007',rdp:'0',",
        },
        "8059": {
            "count": len(events[8059]), "count_per_hour": len(events[8059]) / hours,
            "cadence_seconds": _cadence(events[8059]),
            "payload_length": _stats([float(item["data_len"]) for item in events[8059]]),
            "variant_counts": dict(Counter(variants_8059)),
            "variant_probability": {
                name: count / len(variants_8059) for name, count in Counter(variants_8059).items()
            },
            "variant_transition": _transition(variants_8059),
            "state_counts": dict(Counter(states_8059)),
            "state_transition": _transition(states_8059),
            "field_presence": dict(Counter(key for row in parsed_8059 for key in row)),
            "observed_values": {
                key: dict(Counter(row.get(key, "") for row in parsed_8059 if key in row))
                for key in ("alarmtype", "alarmnum")
            },
        },
        "9053": {
            "count": len(events[9053]), "count_per_hour": len(events[9053]) / hours,
            "cadence_seconds": _cadence(events[9053]),
            "payload_length": _stats([float(item["data_len"]) for item in events[9053]]),
            "batch_size": _stats(batch_sizes),
            "empty_batches": empty_batches,
            "encoding_styles": dict(encoding_styles),
            "category_codes": dict(category_codes.most_common()),
            "payload_time_minus_outer_seconds": _stats(payload_time_lags),
            "payload_time_minus_event_time_seconds": _stats(event_time_lags),
            "top_level_fields": ["source", "uuid", "hostid", "time", "logdatas"],
            "log_fields_observed": "timestamp|unknown_code_1|category_code_1|category_code_2|category_code_3|...|message",
        },
        "9055": {
            "count": len(events[9055]), "count_per_hour": len(events[9055]) / hours,
            "payload_length": _stats([float(item["data_len"]) for item in events[9055]]),
            "startup_9055_to_9050_seconds": _stats(startup_offsets),
            "batch_sizes": [len(item.get("logdatas", [])) for item in parsed_9055],
            "top_level_fields": ["source", "uuid", "hostid", "time", "logdatas"],
        },
        "9056": {
            "count": len(events[9056]), "count_per_hour": len(events[9056]) / hours,
            "cadence_seconds": _cadence(events[9056]),
            "payload_length": _stats([float(item["data_len"]) for item in events[9056]]),
            "tablename_values": dict(Counter(str(item.get("tablename", "")) for item in parsed_9056)),
            "columnname_values": dict(Counter(str(item.get("columnname", "")) for item in parsed_9056)),
            "row_widths": {str(key): value for key, value in row_widths.items()},
            "state_counts": dict(Counter(states_9056)),
            "state_transition": _transition(states_9056),
            "time_minus_createtime_seconds": _stats(time_create_delta),
            "row_time_minus_outer_seconds": _stats(payload_outer_lag),
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = analyze(args.events)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
