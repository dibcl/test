import unittest

from ice_inside_protocol import (
    IceInsideProtocolError,
    build_8047,
    build_8047_host_payload,
    parse_8047,
    parse_8047_host_payload,
)


UUID = "11111111-1111-4111-8111-111111111111"


class IceInsideProtocolTests(unittest.TestCase):
    def test_8047_round_trip_matches_confirmed_ice_display_format(self):
        encoded = build_8047(1493, UUID)
        self.assertEqual(encoded, (
            b"msgtype=8047;msgdata={msgtype:'8047',msgid:'1493',"
            b"vmuuid:'11111111-1111-4111-8111-111111111111'};"
        ))
        parsed = parse_8047(encoded)
        self.assertEqual(parsed.msgtype, 8047)
        self.assertEqual(parsed.msgid, 1493)
        self.assertEqual(parsed.vmuuid, UUID)

    def test_8047_rejects_extra_fields_bad_uuid_and_overflow(self):
        with self.assertRaises(IceInsideProtocolError):
            parse_8047(
                "msgtype=8047;msgdata={msgtype:'8047',msgid:'1',"
                f"vmuuid:'{UUID}',extra:'x'}};"
            )
        with self.assertRaises(IceInsideProtocolError):
            build_8047(1, "not-a-uuid")
        with self.assertRaises(IceInsideProtocolError):
            build_8047(0x100000000, UUID)

    def test_8047_host_payload_uses_forwarded_inner_object_only(self):
        raw = build_8047_host_payload(1493, UUID)
        self.assertEqual(raw, (
            b"{msgtype:'8047',msgid:'1493',"
            b"vmuuid:'11111111-1111-4111-8111-111111111111'}"
        ))
        self.assertEqual(parse_8047_host_payload(raw).msgid, 1493)


if __name__ == "__main__":
    unittest.main()
