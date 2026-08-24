"""Extract redacted protocol schemas from a local Windows vmswitch log."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


MESSAGE_IDS = (4002, 4004, 8047, 9050, 9054)
LINE_PATTERN = re.compile(
    r"ret=(?P<ret>\d+).*?int_msgid=(?P<id>\d+).*?data_len=(?P<data_len>\d+).*?msg=(?P<payload>.*)$"
)
EVENT_4004 = re.compile(
    r"\A\{msgtype:'4004',vmid:'([^']*)',vmbooster:'([^']*)',vmagent:' ',"
    r"PVDriver:'([^']*)',vdagent:'([^']*)',usbipc:'([^']*)',media_redirect:'([^']*)'\}\Z"
)
EVENT_8047 = re.compile(
    r"\A\{msgtype:'8047',msgid:'([0-9]+)',vmuuid:'([^']+)'\}\Z"
)
VERSION_PATTERN = re.compile(r"\A(?:TEST-)?[Vv]?\d+(?:[.\-_A-Za-z0-9]+)*\Z")
UUID_PATTERN = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
DATETIME_PATTERN = re.compile(r"\A\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
MEMORY_PATTERN = re.compile(r"\A\d+M\Z")
INTEGER_TEXT_PATTERN = re.compile(r"\A\d+\Z")


class SchemaExtractionError(ValueError):
    pass


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def string_format(value: str) -> str:
    if value == "":
        return "empty"
    if value == "0" * 36:
        return "zero36"
    if UUID_PATTERN.fullmatch(value):
        return "uuid"
    if DATETIME_PATTERN.match(value):
        return "datetime"
    if MEMORY_PATTERN.fullmatch(value):
        return "memory_mb"
    if INTEGER_TEXT_PATTERN.fullmatch(value):
        return "integer_text"
    if "%" in value or "+" in value:
        return "url_encoded_or_literal_plus"
    if VERSION_PATTERN.fullmatch(value):
        return "version_like"
    return "string"


class Shape:
    def __init__(self) -> None:
        self.types: dict[str, set[str]] = defaultdict(set)
        self.orders: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        self.formats: dict[str, set[str]] = defaultdict(set)

    def observe(self, value: Any, path: str = "$") -> None:
        kind = type_name(value)
        self.types[path].add(kind)
        if isinstance(value, str):
            self.formats[path].add(string_format(value))
        elif isinstance(value, dict):
            self.orders[path].add(tuple(str(key) for key in value.keys()))
            for key, item in value.items():
                self.observe(item, f"{path}.{key}")
        elif isinstance(value, list):
            for item in value:
                self.observe(item, f"{path}[*]")

    def as_dict(self) -> dict[str, Any]:
        return {
            "paths": {key: sorted(value) for key, value in sorted(self.types.items())},
            "object_key_orders": {
                key: [list(order) for order in sorted(value)]
                for key, value in sorted(self.orders.items())
            },
            "string_formats": {
                key: sorted(value) for key, value in sorted(self.formats.items())
            },
        }


def parse_payload(msgid: int, payload: str | bytes) -> dict[str, Any]:
    if isinstance(payload, bytes):
        text = payload.rstrip(b"\x00").decode("utf-8")
    else:
        text = payload
    if msgid in {4002, 9050, 9054}:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise SchemaExtractionError(f"{msgid} payload is not an object")
        return value
    if msgid == 4004:
        match = EVENT_4004.fullmatch(text)
        if match is None:
            raise SchemaExtractionError("4004 payload does not match observed plaintext schema")
        vmid, vmbooster, pvdriver, vdagent, usbipc, media = match.groups()
        return {
            "msgtype": "4004",
            "vmid": vmid,
            "vmbooster": vmbooster,
            "vmagent": " ",
            "PVDriver": pvdriver,
            "vdagent": vdagent,
            "usbipc": usbipc,
            "media_redirect": media,
        }
    if msgid == 8047:
        match = EVENT_8047.fullmatch(text)
        if match is None:
            raise SchemaExtractionError("8047 payload does not match observed Host schema")
        return {"msgtype": "8047", "msgid": match.group(1), "vmuuid": match.group(2)}
    raise SchemaExtractionError(f"unsupported message ID: {msgid}")


def extract(log_path: str | Path) -> dict[str, Any]:
    source = Path(log_path).resolve()
    shapes = {msgid: Shape() for msgid in MESSAGE_IDS}
    counts = defaultdict(int)
    parse_errors = defaultdict(int)
    lengths: dict[int, set[int]] = defaultdict(set)
    expansions: dict[int, set[int]] = defaultdict(set)
    versions: dict[str, set[str]] = defaultdict(set)
    latest_versions: dict[str, str] = {}
    digest = sha256()
    with source.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            match = LINE_PATTERN.search(line)
            if match is None:
                continue
            msgid = int(match.group("id"))
            if msgid not in shapes:
                continue
            counts[msgid] += 1
            data_len = int(match.group("data_len"))
            ret = int(match.group("ret"))
            lengths[msgid].add(data_len)
            expansions[msgid].add(ret - 0x80 - data_len - 1)
            try:
                value = parse_payload(msgid, match.group("payload"))
            except (SchemaExtractionError, UnicodeDecodeError, json.JSONDecodeError):
                parse_errors[msgid] += 1
                continue
            shapes[msgid].observe(value)
            if msgid == 4002 and isinstance(value.get("agentversion"), str):
                versions["vmbooster_heartbeat"].add(value["agentversion"])
                latest_versions["vmbooster_heartbeat"] = value["agentversion"]
            elif msgid == 4004:
                for key in ("vmbooster", "PVDriver", "vdagent", "usbipc", "media_redirect"):
                    if value.get(key):
                        versions[key].add(str(value[key]))
                    latest_versions[key] = str(value.get(key, ""))
            elif msgid == 9050:
                environment = value.get("environment", {})
                if isinstance(environment, dict) and environment.get("version"):
                    versions["guesttools_environment"].add(str(environment["version"]))
                    latest_versions["guesttools_environment"] = str(environment["version"])

    messages = {}
    for msgid in MESSAGE_IDS:
        messages[str(msgid)] = {
            "format": "json" if msgid in {4002, 9050, 9054} else "plaintext_object",
            "samples": counts[msgid],
            "parse_errors": parse_errors[msgid],
            "data_len_values": sorted(lengths[msgid]),
            "serial_escape_expansion_values": sorted(expansions[msgid]),
            **shapes[msgid].as_dict(),
        }
    return {
        "schema_version": 1,
        "redacted": True,
        "source": {
            "kind": "local_vmswitch_log",
            "path": str(source),
            "sha256": digest.hexdigest(),
        },
        "messages": messages,
        "standard_versions": {key: sorted(value) for key, value in sorted(versions.items())},
        "latest_observed_versions": dict(sorted(latest_versions.items())),
        "component_names": [
            "vmbooster", "vmagent", "PVDriver", "vdagent", "usbipc", "media_redirect"
        ],
        "fixed_defaults": {
            "4002.msgtype": "4002",
            "4002.agentstatus": "1",
            "4002.data_len": 512,
            "4004.msgtype": "4004",
            "4004.vmagent": " ",
            "4004.data_len": 512,
            "9050.source": 4,
            "9050.hostid": "000000000000000000000000000000000000",
            "9050.environment.targetversion": "",
            "9054.source": 4,
            "9054.mothod": "1",
        },
    }


def compare_fixtures(schema: dict[str, Any], fixtures_dir: str | Path) -> dict[str, Any]:
    root = Path(fixtures_dir)
    result = {}
    for msgid in MESSAGE_IDS:
        fixture_path = root / f"{msgid}.payload.bin"
        if not fixture_path.exists():
            result[str(msgid)] = {"aligned": False, "error": "fixture_missing"}
            continue
        try:
            value = parse_payload(msgid, fixture_path.read_bytes())
        except (SchemaExtractionError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            result[str(msgid)] = {"aligned": False, "error": str(exc)}
            continue
        fixture_shape = Shape()
        fixture_shape.observe(value)
        fixture = fixture_shape.as_dict()
        observed = schema["messages"][str(msgid)]
        missing_paths = sorted(set(observed["paths"]) - set(fixture["paths"]))
        extra_paths = sorted(set(fixture["paths"]) - set(observed["paths"]))
        type_mismatches = {
            path: {"observed": observed["paths"][path], "fixture": fixture["paths"][path]}
            for path in set(observed["paths"]) & set(fixture["paths"])
            if observed["paths"][path] != fixture["paths"][path]
        }
        order_mismatches = {}
        for path, observed_orders in observed["object_key_orders"].items():
            fixture_orders = fixture["object_key_orders"].get(path, [])
            if any(order not in observed_orders for order in fixture_orders):
                order_mismatches[path] = {
                    "observed": observed_orders,
                    "fixture": fixture_orders,
                }
        result[str(msgid)] = {
            "aligned": not missing_paths and not extra_paths and not type_mismatches and not order_mismatches,
            "missing_paths": missing_paths,
            "extra_paths": extra_paths,
            "type_mismatches": type_mismatches,
            "order_mismatches": order_mismatches,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fixtures-dir")
    args = parser.parse_args()
    result = extract(args.log)
    if args.fixtures_dir:
        result["fixture_alignment"] = compare_fixtures(result, args.fixtures_dir)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "output": str(Path(args.output).resolve()),
        "samples": {key: value["samples"] for key, value in result["messages"].items()},
        "aligned": {
            key: value["aligned"] for key, value in result.get("fixture_alignment", {}).items()
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
