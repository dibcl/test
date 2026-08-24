from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "extract_profile_from_logs", ROOT / "tools" / "extract_profile_from_logs.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ExtractProfileFromLogsTests(unittest.TestCase):
    def test_preserves_protocol_literals_but_redacts_endpoint_identity(self) -> None:
        payload = (
            '{"msgtype":"4002","agentversion":"V7.25.21SP3pv",'
            '"vmid":"123456789012345678901234567890123456",'
            '"agentstatus":"1","computername":"REAL-HOST","issysprep":"0"}'
        )
        line = f"ret=641 int_msgid=4002 data_len=512 msg={payload}\n"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "vmswitch.log"
            source.write_text(line, encoding="utf-8")
            result = MODULE.extract(source)
        profile = next(iter(result["messages"]["4002"]["profiles"].values()))
        redacted = profile["payload"]
        self.assertEqual(redacted["msgtype"], "4002")
        self.assertEqual(redacted["agentversion"], "V7.25.21SP3pv")
        self.assertNotEqual(redacted["vmid"], "123456789012345678901234567890123456")
        self.assertNotEqual(redacted["computername"], "REAL-HOST")
        self.assertEqual(len(redacted["vmid"]), 36)
        self.assertIn("$.vmid", profile["field_commitments"])

    def test_retains_every_record_and_stable_tokens(self) -> None:
        payload = "{msgtype:'8047',msgid:'1234567890',vmuuid:'11111111-1111-4111-8111-111111111111'}"
        line = f"ret=210 int_msgid=8047 data_len=81 msg={payload}\n"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "vmswitch.log"
            source.write_text(line + line, encoding="utf-8")
            result = MODULE.extract(source)
        message = result["messages"]["8047"]
        self.assertEqual(message["record_count"], 2)
        self.assertEqual(len(message["profiles"]), 1)
        profile = next(iter(message["profiles"].values()))
        self.assertEqual(profile["occurrences"], 2)
        self.assertNotEqual(profile["payload"]["vmuuid"], "11111111-1111-4111-8111-111111111111")


if __name__ == "__main__":
    unittest.main()
