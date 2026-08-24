import os
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from extract_protocol_schema import compare_fixtures, extract
from generate_mswitch_fixtures import generate
from mock_telemetry_agent import FrozenProfile


ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROFILE = ROOT / "lab" / "mock-telemetry" / "baseline.synthetic.json"


class ExtractProtocolSchemaTests(unittest.TestCase):
    def test_extracts_redacted_paths_orders_versions_and_alignment(self):
        lines = [
            'x ret=641 dst_type=1 int_msgid=4002 dst_mod=1 data_len=512,msg={"msgtype":"4002","agentversion":"V1.2","vmid":"secret","agentstatus":"1","computername":"secret","issysprep":"0"}',
            "x ret=641 dst_type=1 int_msgid=4004 dst_mod=1 data_len=512,msg={msgtype:'4004',vmid:'secret',vmbooster:'V1.2',vmagent:' ',PVDriver:'3.0',vdagent:'',usbipc:'',media_redirect:''}",
            "x ret=210 dst_type=1 int_msgid=8047 dst_mod=1 data_len=81,msg={msgtype:'8047',msgid:'2030010100',vmuuid:'11111111-1111-4111-8111-111111111111'}",
            'x ret=609 dst_type=1 int_msgid=9050 dst_mod=10 data_len=480,msg={"source":4,"uuid":"11111111-1111-4111-8111-111111111111","hostid":"000000000000000000000000000000000000","time":"2030-01-01T00:00:00.000+00:00","groupid":"-1","createtime":"2030-01-01T00:00:00.000+00:00","environment":{"computername":"TEST-WIN10","cpu":"Synthetic CPU","os":"Microsoft+Windows","bit":"64","mem":"16384M","mac":"02-00-00-00-00-10","ip":"192.0.2.10","disk":"C:80GB","diskused":"C:20GB","version":"V1.2","targetversion":""}}',
            'x ret=300 dst_type=1 int_msgid=9054 dst_mod=10 data_len=171,msg={"source":4,"uuid":"11111111-1111-4111-8111-111111111111","hostid":"secret","createtime":"2030-01-01T00:00:00.000+00:00","mothod":"1","softwares":[{"name":"Synthetic+App","type":"1","publisher":"Vendor","installtime":"20300101","size":"0","version":"1.0","operate":"1"}]}',
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "fixture.log"
            log.write_text("\n".join(lines), encoding="utf-8")
            schema = extract(log)
            fixtures = root / "fixtures"
            generate(FrozenProfile.load(str(PROFILE)), fixtures)
            alignment = compare_fixtures(schema, fixtures)
        self.assertTrue(schema["redacted"])
        self.assertEqual(schema["standard_versions"]["vmbooster_heartbeat"], ["V1.2"])
        self.assertEqual(schema["latest_observed_versions"]["PVDriver"], "3.0")
        self.assertIn("$.environment.targetversion", schema["messages"]["9050"]["paths"])
        self.assertNotIn("secret", str(schema))
        self.assertTrue(all(item["aligned"] for item in alignment.values()))


if __name__ == "__main__":
    unittest.main()
