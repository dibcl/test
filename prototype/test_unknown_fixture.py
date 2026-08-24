import unittest

from mswitch_protocol import build_message
from unknown_fixture import UnknownFixtureRecorder


class UnknownFixtureTests(unittest.TestCase):
    def test_8047_records_header_hash_and_redacted_payload_without_reply(self):
        raw = build_message(
            dst_mod=0x80000001,
            uuid=b"U" * 16,
            dst_type=0,
            int_msgid=8047,
            payload=b'{"secret":"fixture"}',
            src_mod=0x80000000,
        ).to_bytes()
        recorder = UnknownFixtureRecorder()
        record = recorder.record_frame(raw)
        self.assertEqual(record.status, "unsupported_fixture")
        self.assertEqual(record.int_msgid, 8047)
        self.assertEqual(len(record.header_hex), 0x80 * 2)
        self.assertEqual(record.payload_length, len(b'{"secret":"fixture"}'))
        self.assertEqual(record.redacted_payload, "<redacted>")
        self.assertEqual(len(record.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
