import os
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from generate_mswitch_fixtures import generate
from mock_telemetry_agent import FrozenProfile
from mswitch_protocol import parse_message


ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROFILE = ROOT / "lab" / "mock-telemetry" / "baseline.synthetic.json"


class GenerateMswitchFixturesTests(unittest.TestCase):
    def test_generation_is_deterministic_and_exports_all_layers(self):
        profile = FrozenProfile.load(str(PROFILE))
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            one = generate(profile, first)
            two = generate(profile, second)
            self.assertEqual(one, two)
            self.assertEqual([item["int_msgid"] for item in one["fixtures"]], [4002, 4004, 8047, 9050, 9054])
            for msgid in (4002, 4004, 8047, 9050, 9054):
                self.assertEqual((first / f"{msgid}.raw.bin").read_bytes(), (second / f"{msgid}.raw.bin").read_bytes())
                message = parse_message((first / f"{msgid}.raw.bin").read_bytes())
                self.assertEqual(message.int_msgid, msgid)
            self.assertNotIn(b"test_mode", (first / "4002.payload.bin").read_bytes())
            self.assertEqual(len((first / "4002.payload.bin").read_bytes()), 512)
            self.assertGreater(len((first / "4002.payload.bin").read_bytes()) - len((first / "4002.payload.bin").read_bytes().rstrip(b"\x00")), 0)
            self.assertTrue((first / "4004.payload.bin").read_bytes().startswith(b"{msgtype:'4004'"))
            self.assertEqual(len((first / "4004.payload.bin").read_bytes()), 512)
            self.assertTrue((first / "8047.payload.bin").read_bytes().startswith(b"{msgtype:'8047'"))
            self.assertEqual(len((first / "8047.payload.bin").read_bytes()), 81)
            self.assertIn(b'"softwares"', (first / "9054.payload.bin").read_bytes())


if __name__ == "__main__":
    unittest.main()
