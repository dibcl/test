"""Extract a privacy-preserving, non-replayable profile from vmswitch logs.

The extractor retains every observed record's ordering, wire lengths and SHA-256
commitments, while replacing endpoint identity and machine-fingerprint values with
stable synthetic tokens.  It intentionally never writes the original payload.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from extract_protocol_schema import MESSAGE_IDS, parse_payload, string_format


LINE_PATTERN = re.compile(
    r"ret=(?P<ret>\d+).*?int_msgid=(?P<id>\d+).*?data_len=(?P<data_len>\d+).*?msg=(?P<payload>.*)$"
)

# Protocol literals and non-instance component versions are useful Ground Truth.
# Everything else is committed by hash and represented with a stable test token.
PRESERVED_PATHS = {
    "$.msgtype",
    "$.agentstatus",
    "$.issysprep",
    "$.agentversion",
    "$.source",
    "$.mothod",
    "$.environment.bit",
    "$.environment.version",
    "$.environment.targetversion",
    "$.softwares[*].type",
    "$.softwares[*].operate",
}
PRESERVED_4004_PATHS = {
    "$.msgtype",
    "$.vmbooster",
    "$.vmagent",
    "$.PVDriver",
    "$.vdagent",
    "$.usbipc",
    "$.media_redirect",
}


class ProfileExtractionError(ValueError):
    pass


def _digest_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _digits(digest: str, length: int) -> str:
    return "".join(str(int(char, 16) % 10) for char in digest)[:length]


def _stable_token(path: str, value: str) -> str:
    digest = _digest_text(value)
    key = path.rsplit(".", 1)[-1].lower()
    if key in {"uuid", "vmuuid"}:
        return f"{digest[:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"
    if key in {"vmid", "hostid"}:
        if value == "0" * 36:
            return value
        return _digits(digest, 36)
    if key == "mac":
        octets = [int(digest[index:index + 2], 16) for index in range(0, 12, 2)]
        octets[0] = (octets[0] | 0x02) & 0xFE
        separator = "-" if "-" in value else ":"
        return separator.join(f"{item:02X}" for item in octets)
    if key == "ip":
        return f"192.0.2.{1 + int(digest[:2], 16) % 253}"
    if key in {"time", "createtime", "installtime", "msgid"}:
        return "2030-01-01 00:00:00" if "time" in key else _digits(digest, 10)
    if key == "computername":
        return f"REDACTED-{digest[:8].upper()}"
    if key in {"name", "publisher"}:
        return f"REDACTED-{key.upper()}-{digest[:10]}"
    if value == "":
        return ""
    return f"REDACTED-{digest[:12]}"


def _commitment(value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    result: dict[str, Any] = {
        "sha256": _digest_text(encoded),
        "json_length": len(encoded.encode("utf-8")),
        "type": type(value).__name__,
    }
    if isinstance(value, str):
        result["string_format"] = string_format(value)
        result["utf8_length"] = len(value.encode("utf-8"))
    return result


def redact_payload(msgid: int, value: Any, path: str = "$") -> tuple[Any, dict[str, Any]]:
    commitments: dict[str, Any] = {}

    def visit(item: Any, current: str) -> Any:
        if isinstance(item, dict):
            return {key: visit(child, f"{current}.{key}") for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child, f"{current}[*]") for child in item]
        commitments[current] = _commitment(item)
        preserve = current in PRESERVED_PATHS or (msgid == 4004 and current in PRESERVED_4004_PATHS)
        if preserve or not isinstance(item, str):
            return item
        return _stable_token(current, item)

    return visit(value, path), commitments


def extract(log_path: str | Path) -> dict[str, Any]:
    source = Path(log_path).resolve()
    source_digest = sha256()
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    profiles: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    parse_errors: dict[str, int] = defaultdict(int)

    with source.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            source_digest.update(raw_line)
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            match = LINE_PATTERN.search(line)
            if match is None:
                continue
            msgid = int(match.group("id"))
            if msgid not in MESSAGE_IDS:
                continue
            payload_text = match.group("payload")
            payload_bytes = payload_text.encode("utf-8")
            try:
                parsed = parse_payload(msgid, payload_text)
            except Exception as exc:  # malformed evidence is counted, never guessed
                parse_errors[str(msgid)] += 1
                records[str(msgid)].append({
                    "line_number": line_number,
                    "parse_error": type(exc).__name__,
                    "payload_sha256": sha256(payload_bytes).hexdigest(),
                    "payload_utf8_length": len(payload_bytes),
                })
                continue

            redacted, commitments = redact_payload(msgid, parsed)
            canonical = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
            profile_id = sha256(canonical.encode("utf-8")).hexdigest()
            ret = int(match.group("ret"))
            data_len = int(match.group("data_len"))
            records[str(msgid)].append({
                "line_number": line_number,
                "ret": ret,
                "data_len": data_len,
                "serial_escape_expansion": ret - 0x80 - data_len - 1,
                "payload_sha256": sha256(payload_bytes).hexdigest(),
                "payload_utf8_length": len(payload_bytes),
                "redacted_profile_id": profile_id,
            })
            profile = profiles[str(msgid)].get(profile_id)
            if profile is None:
                profiles[str(msgid)][profile_id] = {
                    "occurrences": 1,
                    "first_line": line_number,
                    "last_line": line_number,
                    "payload": redacted,
                    "field_commitments": commitments,
                }
            else:
                profile["occurrences"] += 1
                profile["last_line"] = line_number

    return {
        "schema_version": 1,
        "test_mode": True,
        "redacted": True,
        "reusable_identity": False,
        "source": {
            "kind": "local_vmswitch_log",
            "path": str(source),
            "sha256": source_digest.hexdigest(),
        },
        "messages": {
            str(msgid): {
                "record_count": len(records[str(msgid)]),
                "parse_errors": parse_errors[str(msgid)],
                "records": records[str(msgid)],
                "profiles": profiles[str(msgid)],
            }
            for msgid in MESSAGE_IDS
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--output", default="lab/mock-telemetry/real_device_profile.redacted.json")
    args = parser.parse_args()
    output = Path(args.output)
    if output.name.lower() == "real_device_profile.json":
        raise ProfileExtractionError("output must be explicitly named *.redacted.json")
    result = extract(args.log)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output.resolve()),
        "source_sha256": result["source"]["sha256"],
        "records": {key: value["record_count"] for key, value in result["messages"].items()},
        "parse_errors": {key: value["parse_errors"] for key, value in result["messages"].items()},
        "redacted": True,
        "reusable_identity": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
