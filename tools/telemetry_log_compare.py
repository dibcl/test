"""Compare observed Windows and validation-runtime telemetry logs statistically."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REAL_SEND = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?"
    r"WriteSerialPort succ.*?int_msgid=(?P<id>-?\d+),.*?msg=(?P<payload>.*)$"
)
REAL_HOST = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?"
    r"send_msg .*?int_msgid=(?P<id>-?\d+),.*?(?:data|msg)=(?P<payload>.*)$"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
PROCESS_GROUPS = (
    "process",
    "process_memory",
    "process_handle",
    "process_diskio",
    "process_netio",
)


@dataclass(frozen=True, slots=True)
class Event:
    timestamp: datetime
    msgid: int | None
    payload: dict[str, Any]
    direction: str

    @property
    def type_key(self) -> str:
        label = str(self.msgid) if self.msgid is not None else "runtime_envelope"
        return f"{self.direction}:{label}"


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        result = datetime.strptime(text, DATE_FORMAT)
    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def _payload(text: str) -> dict[str, Any]:
    value = text.strip().rstrip(".")
    if value.startswith("{"):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            return decoded
        pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*):'([^']*)'", value)
        if pairs:
            return dict(pairs)
    msgtype = re.search(r"msgtype[=:][\"']?(\d+)", value)
    return {"msgtype": msgtype.group(1)} if msgtype else {"_format": "plaintext"}


def _files(
    paths: Iterable[str | Path],
    suffixes: set[str] | None = None,
) -> list[Path]:
    allowed = suffixes or {".log", ".txt", ".jsonl"}
    result: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            result.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in allowed
            )
        elif path.is_file():
            result.append(path)
        else:
            raise FileNotFoundError(path)
    return sorted(set(result))


def collect_real(paths: Iterable[str | Path]) -> list[Event]:
    events: list[Event] = []
    for path in _files(paths, {".log", ".txt"}):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = REAL_SEND.match(line)
                direction = "guest_to_host"
                if match is None:
                    match = REAL_HOST.match(line)
                    direction = "host_to_guest"
                if match is None:
                    continue
                msgid = int(match.group("id")) & 0xFFFFFFFF
                payload = _payload(match.group("payload"))
                if msgid == 0 and isinstance(payload.get("msgtype"), str):
                    msgid = int(payload["msgtype"])
                events.append(Event(
                    datetime.strptime(match.group("time"), DATE_FORMAT),
                    msgid,
                    payload,
                    direction,
                ))
    return sorted(events, key=lambda item: item.timestamp)


def collect_runtime(paths: Iterable[str | Path]) -> list[Event]:
    events: list[Event] = []
    for path in _files(paths, {".jsonl"}):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSONL") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: JSONL row must be an object")
                if "int_msgid" in row:
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError(f"{path}:{line_number}: payload must be an object")
                    events.append(Event(
                        _timestamp(row.get("emitted_at")),
                        int(row["int_msgid"]),
                        payload,
                        "guest_to_host",
                    ))
                elif "metrics" in row:
                    metrics = row.get("metrics")
                    if not isinstance(metrics, dict):
                        raise ValueError(f"{path}:{line_number}: metrics must be an object")
                    events.append(Event(
                        _timestamp(row.get("observed_at")),
                        None,
                        metrics,
                        "runtime",
                    ))
    return sorted(events, key=lambda item: item.timestamp)


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return {
            "count": 0,
            "min": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p95": None,
            "max": None,
            "mean": None,
            "stddev": None,
        }
    rounded = lambda value: round(float(value), 6)
    return {
        "count": len(finite),
        "min": rounded(min(finite)),
        "p25": rounded(_quantile(finite, 0.25)),
        "p50": rounded(_quantile(finite, 0.50)),
        "p75": rounded(_quantile(finite, 0.75)),
        "p95": rounded(_quantile(finite, 0.95)),
        "max": rounded(max(finite)),
        "mean": rounded(statistics.fmean(finite)),
        "stddev": rounded(statistics.pstdev(finite)),
    }


def _intervals(events: list[Event]) -> dict[str, list[float]]:
    grouped: dict[str, list[datetime]] = defaultdict(list)
    for event in events:
        grouped[event.type_key].append(event.timestamp)
    return {
        key: [
            (right - left).total_seconds()
            for left, right in zip(values, values[1:])
        ]
        for key, values in grouped.items()
    }


def _timeline(events: list[Event]) -> dict[str, Any]:
    if not events:
        return {"event_count": 0, "first": None, "last": None, "duration_seconds": None, "first_events": []}
    start = events[0].timestamp
    return {
        "event_count": len(events),
        "first": events[0].timestamp.isoformat(),
        "last": events[-1].timestamp.isoformat(),
        "duration_seconds": round((events[-1].timestamp - start).total_seconds(), 6),
        "first_events": [
            {
                "offset_seconds": round((event.timestamp - start).total_seconds(), 6),
                "type": event.type_key,
            }
            for event in events[:20]
        ],
    }


def timeline_difference(real: list[Event], runtime: list[Event]) -> dict[str, Any]:
    real_start = real[0].timestamp if real else None
    runtime_start = runtime[0].timestamp if runtime else None
    rows = []
    for key in sorted({event.type_key for event in real} | {event.type_key for event in runtime}):
        left = [event for event in real if event.type_key == key]
        right = [event for event in runtime if event.type_key == key]
        left_offset = (left[0].timestamp - real_start).total_seconds() if left and real_start else None
        right_offset = (right[0].timestamp - runtime_start).total_seconds() if right and runtime_start else None
        rows.append({
            "type": key,
            "real_count": len(left),
            "runtime_count": len(right),
            "count_delta": len(right) - len(left),
            "real_first_offset_seconds": left_offset,
            "runtime_first_offset_seconds": right_offset,
            "first_offset_delta_seconds": (
                right_offset - left_offset
                if left_offset is not None and right_offset is not None
                else None
            ),
        })
    return {"real": _timeline(real), "runtime": _timeline(runtime), "by_type": rows}


def _proportions(events: list[Event]) -> dict[str, Any]:
    counts = Counter(event.type_key for event in events)
    payload_counts = Counter(
        str(event.payload["msgtype"])
        for event in events
        if "msgtype" in event.payload
    )
    total = sum(counts.values())
    payload_total = sum(payload_counts.values())
    return {
        "message_type": {
            key: {"count": value, "share": round(value / total, 8) if total else 0.0}
            for key, value in sorted(counts.items())
        },
        "payload_msgtype": {
            key: {"count": value, "share": round(value / payload_total, 8) if payload_total else 0.0}
            for key, value in sorted(payload_counts.items())
        },
    }


def period_deviation(real: list[Event], runtime: list[Event]) -> dict[str, Any]:
    left = _intervals(real)
    right = _intervals(runtime)
    result = {}
    for key in sorted(set(left) | set(right)):
        left_stats = distribution(left.get(key, []))
        right_stats = distribution(right.get(key, []))
        left_median = left_stats["p50"]
        right_median = right_stats["p50"]
        result[key] = {
            "real_seconds": left_stats,
            "runtime_seconds": right_stats,
            "median_delta_seconds": (
                round(float(right_median) - float(left_median), 6)
                if left_median is not None and right_median is not None
                else None
            ),
        }
    return result


def _type_name(value: Any) -> str:
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


def _schema(value: Any) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)

    def visit(item: Any, path: str) -> None:
        result[path].add(_type_name(item))
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{path}.{key}")
        elif isinstance(item, list):
            for child in item:
                visit(child, f"{path}[*]")

    visit(value, "$")
    return result


def field_differences(real: list[Event], runtime: list[Event]) -> dict[str, Any]:
    schemas: dict[str, dict[str, dict[str, set[str]]]] = {
        "real": defaultdict(lambda: defaultdict(set)),
        "runtime": defaultdict(lambda: defaultdict(set)),
    }
    for label, events in (("real", real), ("runtime", runtime)):
        for event in events:
            for path, types in _schema(event.payload).items():
                schemas[label][event.type_key][path].update(types)
    output = {}
    for key in sorted(set(schemas["real"]) | set(schemas["runtime"])):
        left = schemas["real"].get(key, {})
        right = schemas["runtime"].get(key, {})
        common = sorted(set(left) & set(right))
        output[key] = {
            "real_only_paths": sorted(set(left) - set(right)),
            "runtime_only_paths": sorted(set(right) - set(left)),
            "type_differences": {
                path: {
                    "real": sorted(left[path]),
                    "runtime": sorted(right[path]),
                }
                for path in common
                if left[path] != right[path]
            },
            "matching_path_count": sum(left[path] == right[path] for path in common),
            "common_path_count": len(common),
        }
    return output


def _process_rows(payload: dict[str, Any]) -> dict[str, list[tuple[str, int]]]:
    source = payload.get("process_snapshot") if "process_snapshot" in payload else payload
    if not isinstance(source, dict):
        return {}
    result: dict[str, list[tuple[str, int]]] = {}
    for group in PROCESS_GROUPS:
        rows = source.get(group)
        if not isinstance(rows, list):
            continue
        parsed = []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("data"), str):
                parts = row["data"].split("|")
                if len(parts) >= 2 and parts[1].isdigit():
                    parsed.append((parts[0], int(parts[1])))
            elif isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("pid"), int):
                parsed.append((row["name"], row["pid"]))
        result[group] = parsed
    return result


def process_trend(events: list[Event]) -> dict[str, Any]:
    snapshots = [
        (event.timestamp, rows)
        for event in events
        if (rows := _process_rows(event.payload))
    ]
    counts: dict[str, list[float]] = defaultdict(list)
    all_names: set[str] = set()
    all_pids: set[int] = set()
    presence: Counter[str] = Counter()
    jaccard: list[float] = []
    pid_transitions = 0
    pid_changes = 0
    previous: dict[str, int] | None = None
    for _, groups in snapshots:
        for group, rows in groups.items():
            counts[group].append(len(rows))
        current = dict(groups.get("process", []))
        all_names.update(current)
        all_pids.update(current.values())
        presence.update(current.keys())
        if previous is not None:
            left_names, right_names = set(previous), set(current)
            union = left_names | right_names
            jaccard.append(len(left_names & right_names) / len(union) if union else 1.0)
            for name in left_names & right_names:
                pid_transitions += 1
                pid_changes += int(previous[name] != current[name])
        previous = current
    snapshot_count = len(snapshots)
    return {
        "snapshot_count": snapshot_count,
        "first": snapshots[0][0].isoformat() if snapshots else None,
        "last": snapshots[-1][0].isoformat() if snapshots else None,
        "row_count_by_group": {key: distribution(value) for key, value in sorted(counts.items())},
        "unique_process_name_count": len(all_names),
        "unique_pid_count": len(all_pids),
        "pid_transition_count": pid_transitions,
        "pid_change_count": pid_changes,
        "pid_change_share": round(pid_changes / pid_transitions, 8) if pid_transitions else None,
        "consecutive_name_jaccard": distribution(jaccard),
        "process_presence_top10": [
            {
                "name": name,
                "snapshot_count": count,
                "share": round(count / snapshot_count, 8) if snapshot_count else 0.0,
            }
            for name, count in presence.most_common(10)
        ],
    }


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _packed_numbers(value: Any) -> list[float]:
    if not isinstance(value, str):
        return []
    result = []
    for item in value.split("|"):
        try:
            result.append(float(item))
        except ValueError:
            continue
    return result


def metric_distributions(events: list[Event]) -> dict[str, Any]:
    values: dict[str, list[float]] = defaultdict(list)
    for event in events:
        payload = event.payload
        if event.msgid == 9051:
            samples = payload.get("performance", [])
            if not isinstance(samples, list):
                continue
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                for name, value in (("cpu_percent", sample.get("cpu")),):
                    if (number := _float(value)) is not None:
                        values[name].append(number)
                mem = sample.get("mem")
                if isinstance(mem, dict) and (number := _float(mem.get("used"))) is not None:
                    values["memory_used_percent"].append(number)
                disk = _packed_numbers(sample.get("disk"))
                for index, number in enumerate(disk):
                    values[f"disk_io_field_{index}"].append(number)
                network = sample.get("network")
                if isinstance(network, list):
                    for row in network:
                        packed = _packed_numbers(row.get("data") if isinstance(row, dict) else None)
                        if len(packed) >= 2:
                            values["network_tx"].append(packed[0])
                            values["network_rx"].append(packed[1])
        elif event.msgid == 9052:
            for group, metric in (("process_diskio", "process_disk_io_total"), ("process_netio", "process_network_io_total")):
                rows = payload.get(group)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    data = row.get("data") if isinstance(row, dict) else None
                    if not isinstance(data, str):
                        continue
                    parts = data.split("|")
                    if len(parts) >= 3:
                        try:
                            values[metric].append(float(parts[2]))
                        except ValueError:
                            pass
        elif event.msgid is None:
            cpu = payload.get("cpu")
            memory = payload.get("memory")
            disk = payload.get("disk_io")
            network = payload.get("network_io")
            if isinstance(cpu, dict) and (number := _float(cpu.get("percent"))) is not None:
                values["cpu_percent"].append(number)
            if isinstance(memory, dict) and (number := _float(memory.get("percent"))) is not None:
                values["memory_used_percent"].append(number)
            if isinstance(disk, dict) and (number := _float(disk.get("activity_rate"))) is not None:
                values["disk_io_activity_rate"].append(number)
            if isinstance(network, dict):
                for key, metric in (("tx_bytes_per_second", "network_tx"), ("rx_bytes_per_second", "network_rx")):
                    if (number := _float(network.get(key))) is not None:
                        values[metric].append(number)
    return {key: distribution(value) for key, value in sorted(values.items())}


def compare(real_paths: Iterable[str | Path], runtime_paths: Iterable[str | Path]) -> dict[str, Any]:
    real_files = _files(real_paths, {".log", ".txt"})
    runtime_files = _files(runtime_paths, {".jsonl"})
    real = collect_real(real_files)
    runtime = collect_runtime(runtime_files)
    return {
        "inputs": {
            "real_files": [str(path) for path in real_files],
            "runtime_files": [str(path) for path in runtime_files],
            "real_event_count": len(real),
            "runtime_event_count": len(runtime),
        },
        "message_timeline_difference": timeline_difference(real, runtime),
        "msgtype_proportions": {
            "real": _proportions(real),
            "runtime": _proportions(runtime),
        },
        "period_deviation": period_deviation(real, runtime),
        "field_differences": field_differences(real, runtime),
        "process_trends": {
            "real": process_trend(real),
            "runtime": process_trend(runtime),
        },
        "metric_distributions": {
            "real": metric_distributions(real),
            "runtime": metric_distributions(runtime),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-log", action="append", required=True)
    parser.add_argument("--runtime-log", action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare(args.real_log, args.runtime_log)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
