import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from protocol_diff import normalize_host_msgid, parse_json_payload, schema


class ProtocolDiffTests(unittest.TestCase):
    def test_normalizes_payload_dispatched_host_messages(self):
        self.assertEqual(
            normalize_host_msgid(0, "[performance];msgtype=4100;vmuuid=redacted;"),
            4100,
        )
        self.assertEqual(
            normalize_host_msgid(0, "msgtype=8009;vuuid=redacted;"),
            8009,
        )
        self.assertEqual(normalize_host_msgid(8064, "msgtype=8064"), 8064)

    def test_schema_reports_paths_and_types_without_values(self):
        result = schema({"name": "secret", "items": [{"count": 1}]})
        self.assertEqual(result["$.name"], {"string"})
        self.assertEqual(result["$.items[].count"], {"int"})
        self.assertNotIn("secret", str(result))

    def test_extracts_payload_dispatched_json_with_trailing_comma(self):
        value = parse_json_payload('msgtype=9502;{"oas_interval":"5",}')
        self.assertEqual(value, {"oas_interval": "5"})


if __name__ == "__main__":
    unittest.main()
