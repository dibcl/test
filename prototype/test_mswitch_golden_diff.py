import os
import sys
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from mswitch_golden_diff import compare
from mswitch_protocol import build_message, encode_serial_frame


class MswitchGoldenDiffTests(unittest.TestCase):
    def setUp(self):
        self.raw = build_message(
            dst_mod=6,
            uuid=b"G" * 16,
            dst_type=1,
            int_msgid=0x8102C4,
            payload=b"\x01",
            src_mod=0x80000001,
        ).to_bytes()

    def test_exact_binary_fixture_matches(self):
        result = compare(self.raw, self.raw, serial_framed=False)
        self.assertTrue(result["equal"])
        self.assertEqual(result["expected"]["magic"], "0x5b5b5b5b")
        self.assertEqual(result["expected"]["data_len"], 1)
        self.assertEqual(result["expected"]["trailing_null_bytes"], 0)

    def test_first_difference_and_serial_unframing(self):
        actual = bytearray(self.raw)
        actual[0x50] ^= 1
        result = compare(
            encode_serial_frame(self.raw),
            encode_serial_frame(bytes(actual)),
            serial_framed=True,
        )
        self.assertFalse(result["equal"])
        self.assertEqual(result["first_mismatch_offset"], 0x50)
        self.assertIn("int_msgid", result["header_field_differences"])

    def test_payload_only_reports_context_without_header_parse(self):
        result = compare(b"abc123", b"abcX23", serial_framed=False, payload_only=True)
        self.assertFalse(result["equal"])
        self.assertEqual(result["comparison_scope"], "payload")
        self.assertEqual(result["first_mismatch_offset"], 3)
        self.assertIsNotNone(result["expected_context_hex"])


if __name__ == "__main__":
    unittest.main()
