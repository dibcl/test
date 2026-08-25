from __future__ import annotations

import json
import socket
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telemetry.clocks import SimulatedClock
from telemetry.config import build_provider
from telemetry.local_env import LocalEnvironmentError, load_local_environment
from telemetry.windows_validation import WindowsValidationProvider
from windows_validation_audit import validate_windows_envelope


PROFILE = ROOT / "baseline.runtime.json"
LOCAL_ENV = ROOT / "local_env.json"
SCHEMA = json.loads((ROOT / "runtime-envelope.schema.json").read_text(encoding="utf-8"))
CONTRACT = json.loads((ROOT / "fidelity_contract.json").read_text(encoding="utf-8"))

EXPECTED = {
    "VMID": "fbfd4bbb-7596-4379-b646-c9ec2245a0a5",
    "UUID": "fbfd4bbb-7596-4379-b646-c9ec2245a0a5",
    "HOSTID": "000000000000000000000000000000000000",
    "COMPUTERNAME": "YD-TY-01",
    "MAC": "FA-16-3E-A6-71-6D",
    "IP": "172.20.176.122",
    "CPU": "Intel Xeon Processor (Icelake)",
    "OS": "Microsoft Windows [版本 10.0.19044.4529]",
    "MEM": "16383M",
    "DISK": "C:79.95GB,D:500.00GB",
}


class LocalEnvironmentTests(unittest.TestCase):
    def test_tracked_local_env_matches_current_windows_baseline_exactly(self) -> None:
        self.assertEqual(load_local_environment(LOCAL_ENV), EXPECTED)

    def test_loader_rejects_missing_and_extra_fields(self) -> None:
        value = dict(EXPECTED)
        value.pop("MAC")
        with self.assertRaises(LocalEnvironmentError):
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as tmp:
                path = Path(tmp) / "missing.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                load_local_environment(path)

        value = dict(EXPECTED)
        value["EXTRA"] = "x"
        with self.assertRaises(LocalEnvironmentError):
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as tmp:
                path = Path(tmp) / "extra.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                load_local_environment(path)


class WindowsValidationProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_is_registered_and_uses_local_env(self) -> None:
        provider = build_provider({
            "provider": {
                "type": "windows_validation",
                "profile": str(PROFILE),
                "local_env": str(LOCAL_ENV),
            }
        })
        self.assertIsInstance(provider, WindowsValidationProvider)
        self.assertEqual(provider.local_environment, EXPECTED)

    async def test_snapshot_preserves_exact_environment_without_host_discovery(self) -> None:
        provider = WindowsValidationProvider(PROFILE, LOCAL_ENV)
        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        first = SimpleNamespace(bytes_sent=1000, bytes_recv=2000)
        second = SimpleNamespace(bytes_sent=1300, bytes_recv=2600)
        forbidden = AssertionError("windows validation must not discover host identity/system state")

        with (
            patch("psutil.net_io_counters", side_effect=[first, second, second]),
            patch("psutil.cpu_percent", side_effect=forbidden),
            patch("psutil.virtual_memory", side_effect=forbidden),
            patch("psutil.disk_usage", side_effect=forbidden),
            patch("psutil.disk_io_counters", side_effect=forbidden),
            patch("psutil.process_iter", side_effect=forbidden),
            patch("socket.gethostname", side_effect=forbidden),
            patch("socket.if_nameindex", side_effect=forbidden),
        ):
            await provider.start()
            provider._last_time = 100.0
            with patch("telemetry.providers.time.monotonic", return_value=102.0):
                snapshot = await provider.snapshot(clock)
            health = await provider.health_check()

        envelope = snapshot.to_envelope(schema_version=1)
        jsonschema.validate(envelope, SCHEMA)
        self.assertTrue(health.healthy)
        self.assertEqual(envelope["provider"], "windows-validation")
        self.assertEqual(envelope["metrics"]["local_environment"], EXPECTED)
        self.assertEqual(envelope["metadata"]["environment_source"], "declared-current-windows-machine")
        self.assertEqual(envelope["metrics"]["network_io"]["tx_bytes_per_second"], 150.0)
        self.assertEqual(envelope["metrics"]["network_io"]["rx_bytes_per_second"], 300.0)
        validate_windows_envelope(envelope, contract=CONTRACT, expected_environment=EXPECTED)

    async def test_audit_rejects_modified_environment(self) -> None:
        provider = WindowsValidationProvider(PROFILE, LOCAL_ENV)
        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        with patch("psutil.net_io_counters", side_effect=[SimpleNamespace(bytes_sent=1, bytes_recv=1), SimpleNamespace(bytes_sent=2, bytes_recv=2)]):
            await provider.start()
            provider._last_time = 100.0
            with patch("telemetry.providers.time.monotonic", return_value=101.0):
                envelope = (await provider.snapshot(clock)).to_envelope(schema_version=1)
        envelope["metrics"]["local_environment"]["COMPUTERNAME"] = "changed"
        with self.assertRaises(AssertionError):
            validate_windows_envelope(envelope, contract=CONTRACT, expected_environment=EXPECTED)


if __name__ == "__main__":
    unittest.main()
