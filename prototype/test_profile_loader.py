from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from mock_telemetry_agent import FrozenProfile
from profile_loader import ProfileLoaderError, load_rendered_profile
from static_payloads import StaticPayloadBuilder


ROOT = Path(__file__).resolve().parent.parent


class ProfileLoaderTests(unittest.TestCase):
    def variables(self) -> dict[str, str]:
        return {
            "ZTE_TEST_VMUUUID": "00000000-0000-4000-8000-000000000001",
            "ZTE_TEST_UUID": "00000000-0000-4000-8000-000000000002",
            "ZTE_TEST_HOSTID": "222222222222222222222222222222222222",
            "ZTE_TEST_VMID": "333333333333333333333333333333333333",
            "ZTE_TEST_MAC": "02-00-00-00-00-10",
            "ZTE_TEST_IP": "192.0.2.10",
            "ZTE_TEST_COMPUTERNAME": "TEST-LOCAL",
        }

    def test_renders_template_and_preserves_schema_types(self) -> None:
        document = load_rendered_profile(
            ROOT / "lab/mock-telemetry/profile.placeholder.synthetic.json",
            environ=self.variables(),
        )
        profile = FrozenProfile.from_dict(document)
        payload = StaticPayloadBuilder.from_profile(profile).environment_payload("2030-01-01T00:00:00Z")
        self.assertEqual(payload["uuid"], "00000000-0000-4000-8000-000000000002")
        self.assertEqual(len(profile.identity["test_vmid"]), 36)
        self.assertEqual(payload["environment"]["mac"], "02-00-00-00-00-10")
        self.assertEqual(payload["environment"]["ip"], "192.0.2.10")
        self.assertIsInstance(payload["source"], int)
        self.assertIsInstance(payload["environment"]["bit"], str)

    def test_environment_overrides_local_file(self) -> None:
        local = {key.removeprefix("ZTE_"): value for key, value in self.variables().items()}
        local["TEST_COMPUTERNAME"] = "TEST-FILE"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_env.json"
            path.write_text(json.dumps(local), encoding="utf-8")
            document = load_rendered_profile(
                ROOT / "lab/mock-telemetry/profile.placeholder.synthetic.json",
                local_env_path=path,
                environ={"ZTE_TEST_COMPUTERNAME": "TEST-ENV"},
            )
        self.assertEqual(document["environment"]["computername"], "TEST-ENV")

    def test_rejects_real_placeholders_and_non_test_networks(self) -> None:
        with self.assertRaises(ProfileLoaderError):
            load_rendered_profile(
                ROOT / "lab/mock-telemetry/profile.placeholder.synthetic.json",
                environ={**self.variables(), "ZTE_TEST_IP": "10.0.0.8"},
            )
        with self.assertRaises(ProfileLoaderError):
            load_rendered_profile(
                ROOT / "lab/mock-telemetry/real_device_profile.redacted.json",
                environ=self.variables(),
            )


if __name__ == "__main__":
    unittest.main()
