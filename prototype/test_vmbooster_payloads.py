import unittest

from vmbooster_payloads import VmboosterPayloadError, build_4004, parse_4004


class VmboosterPayloadTests(unittest.TestCase):
    def test_4004_round_trip_matches_static_pe_format(self):
        raw = build_4004(
            vmid="vm-1",
            vmbooster="V7.25",
            PVDriver="100.1",
            vdagent="2.0",
            usbipc="3.0",
            media_redirect="4.0",
        )
        self.assertEqual(raw, (
            b"{msgtype:'4004',vmid:'vm-1',vmbooster:'V7.25',vmagent:' ',"
            b"PVDriver:'100.1',vdagent:'2.0',usbipc:'3.0',media_redirect:'4.0'}"
        ))
        parsed = parse_4004(raw)
        self.assertEqual(parsed.vmid, "vm-1")
        self.assertEqual(parsed.PVDriver, "100.1")
        self.assertEqual(parsed.as_payload()["vmagent"], " ")

    def test_4004_rejects_missing_fields_and_quote_injection(self):
        with self.assertRaises(VmboosterPayloadError):
            parse_4004("{msgtype:'4004'}")
        with self.assertRaises(VmboosterPayloadError):
            build_4004(
                vmid="vm'1",
                vmbooster="V7",
                PVDriver="1",
                vdagent="1",
                usbipc="1",
                media_redirect="1",
            )


if __name__ == "__main__":
    unittest.main()
