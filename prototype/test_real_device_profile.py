from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest

from mock_guest_session import BidirectionalGuestSession, TestHostResponder as HostResponder
from mock_telemetry_agent import FrozenProfile, InMemoryTransport, MockTelemetryAgent
from mswitch_protocol import build_message, encode_serial_frame
from real_device_profile import RealDeviceProfile, RealDeviceProfileError


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "lab", "mock-telemetry", "baseline.synthetic.json")
UUID = "44444444-4444-4444-8444-444444444444"
TEST_UUID = "55555555-5555-4555-8555-555555555555"
TEST_VMID = "vmid-independent-fixture"
TEST_HOSTID = "hostid-independent-fixture"


class RealDeviceProfileTests(unittest.TestCase):
    def _write_profile(self, root, frame_bytes, **overrides):
        frame_path = root / "fixtures" / "8047.bin"
        frame_path.parent.mkdir(exist_ok=True)
        frame_path.write_bytes(frame_bytes)
        value = {
            "schema_version": 1,
            "test_mode": True,
            "redacted": True,
            "vmuuid": UUID,
            "test_uuid": TEST_UUID,
            "test_vmid": TEST_VMID,
            "test_hostid": TEST_HOSTID,
            "software_versions": {
                "vmbooster": "TEST-A-2",
                "PVDriver": "TEST-D-2",
                "vdagent": "TEST-VDA-2",
                "usbipc": "TEST-USB-2",
                "media_redirect": "TEST-MEDIA-2",
            },
            "module_ids": {
                "host": 100,
                "vmbooster": 101,
                "qoe": 102,
                "qoe_target": 103,
                "aux_host": 104,
            },
            "golden_frames": [{
                "name": "quit",
                "path": "fixtures/8047.bin",
                "encoding": "serial",
                "sha256": sha256(frame_bytes).hexdigest(),
                "int_msgid": 8047,
            }],
        }
        value.update(overrides)
        profile_path = root / "profile.json"
        profile_path.write_text(json.dumps(value), encoding="utf-8")
        return profile_path

    def test_loads_hashed_frame_and_injects_versions_and_module_ids(self):
        raw = build_message(
            dst_mod=100,
            uuid=b"F" * 16,
            dst_type=0,
            int_msgid=8047,
            payload=b"fixture",
            src_mod=101,
        ).to_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = self._write_profile(root, encode_serial_frame(raw))
            external = RealDeviceProfile.load(profile_path)
            applied = external.apply_to_frozen(FrozenProfile.load(BASELINE))
        self.assertEqual(external.frame("quit").raw, raw)
        self.assertEqual(external.vmuuid, UUID)
        self.assertEqual(applied.identity["test_uuid"], TEST_UUID)
        self.assertEqual(applied.identity["test_vmid"], TEST_VMID)
        self.assertEqual(applied.identity["test_hostid"], TEST_HOSTID)
        self.assertEqual(applied.identity["agent_version"], "TEST-A-2")
        self.assertEqual(applied.identity["module_ids"]["qoe"], 102)

        transport = InMemoryTransport(responder=HostResponder())
        session = BidirectionalGuestSession(transport, applied.identity, applied.environment)
        agent = MockTelemetryAgent(applied, transport, control_session=session)
        agent.start()
        startup = next(item for item in transport.messages if item.int_msgid == 9050)
        handshake = next(item for item in transport.messages if item.int_msgid == 8008)
        self.assertEqual(startup.source_module, 102)
        self.assertEqual(startup.destination_module, 103)
        self.assertEqual(handshake.source_module, 101)
        self.assertEqual(handshake.destination_module, 100)
        version = next(item for item in transport.messages if item.int_msgid == 4004)
        self.assertIn(b"vmid:'vmid-independent-fixture'", version.wire_payload)
        self.assertIn(b"PVDriver:'TEST-D-2'", version.wire_payload)

    def test_rejects_unredacted_profile_and_hash_mismatch(self):
        raw = build_message(
            dst_mod=1,
            uuid=b"F" * 16,
            dst_type=0,
            int_msgid=8047,
        ).to_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._write_profile(root, raw, redacted=False)
            with self.assertRaises(RealDeviceProfileError):
                RealDeviceProfile.load(path)
            path = self._write_profile(root, raw)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["golden_frames"][0]["sha256"] = "0" * 64
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(RealDeviceProfileError):
                RealDeviceProfile.load(path)


if __name__ == "__main__":
    unittest.main()
