"""Offline byte-for-byte comparison for local Mswitch fixture files."""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOTYPE = os.path.join(ROOT, "prototype")
if PROTOTYPE not in sys.path:
    sys.path.insert(0, PROTOTYPE)

from mswitch_protocol import ProtocolError, SerialFrameDecoder, parse_message


def decode_single_serial_frame(raw: bytes) -> bytes:
    decoder = SerialFrameDecoder()
    frames = decoder.feed(raw)
    decoder.finish()
    if len(frames) != 1:
        raise ProtocolError(f"expected exactly one serial frame, got {len(frames)}")
    return frames[0]


def _context(raw: bytes, offset: int | None, radius: int = 8) -> str | None:
    if offset is None:
        return None
    start = max(0, offset - radius)
    end = min(len(raw), offset + radius + 1)
    return raw[start:end].hex()


def compare(
    expected: bytes,
    actual: bytes,
    serial_framed: bool,
    payload_only: bool = False,
) -> dict[str, object]:
    if serial_framed:
        expected = decode_single_serial_frame(expected)
        actual = decode_single_serial_frame(actual)
    limit = min(len(expected), len(actual))
    mismatch = next((index for index in range(limit) if expected[index] != actual[index]), None)
    if mismatch is None and len(expected) != len(actual):
        mismatch = limit
    result: dict[str, object] = {
        "equal": mismatch is None,
        "expected_length": len(expected),
        "actual_length": len(actual),
        "first_mismatch_offset": mismatch,
        "expected_context_hex": _context(expected, mismatch),
        "actual_context_hex": _context(actual, mismatch),
    }
    if payload_only:
        result["comparison_scope"] = "payload"
        return result
    parsed = {}
    for label, raw in (("expected", expected), ("actual", actual)):
        try:
            message = parse_message(raw)
            parsed[label] = message
            result[label] = {
                "magic": f"0x{message.magic:08x}",
                "version": message.version,
                "int_msgid": f"0x{message.int_msgid:08x}",
                "data_len": message.data_len,
                "visible_payload_length": len(message.payload.rstrip(b"\x00")),
                "trailing_null_bytes": len(message.payload) - len(message.payload.rstrip(b"\x00")),
            }
        except ProtocolError as exc:
            result[label] = {"parse_error": str(exc)}
    if len(parsed) == 2:
        left = parsed["expected"]
        right = parsed["actual"]
        fields = ("magic", "version", "msgtype", "dst_type", "uuid", "src_mod", "dst_mod", "int_msgid", "data_len")
        result["header_field_differences"] = {
            name: {
                "expected": getattr(left, name).hex() if isinstance(getattr(left, name), bytes) else getattr(left, name),
                "actual": getattr(right, name).hex() if isinstance(getattr(right, name), bytes) else getattr(right, name),
            }
            for name in fields if getattr(left, name) != getattr(right, name)
        }
        payload_limit = min(len(left.payload), len(right.payload))
        payload_mismatch = next(
            (index for index in range(payload_limit) if left.payload[index] != right.payload[index]),
            None,
        )
        if payload_mismatch is None and len(left.payload) != len(right.payload):
            payload_mismatch = payload_limit
        result["payload_equal"] = payload_mismatch is None
        result["payload_first_mismatch_offset"] = payload_mismatch
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected")
    parser.add_argument("actual")
    parser.add_argument("--serial-framed", action="store_true")
    parser.add_argument("--payload-only", action="store_true")
    args = parser.parse_args()
    with open(args.expected, "rb") as handle:
        expected = handle.read()
    with open(args.actual, "rb") as handle:
        actual = handle.read()
    result = compare(expected, actual, args.serial_framed, args.payload_only)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
