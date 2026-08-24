from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema

from telemetry.clocks import SimulatedClock
from telemetry.config import build_clock, build_provider, build_transport
from telemetry.model import TelemetrySnapshot
from telemetry.providers import (
    BaseMetricsProvider,
    HybridSyntheticNetworkProvider,
    LiveSystemProvider,
)
from telemetry.registry import Registry
from telemetry.transports import MemoryTransport


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "runtime-envelope.schema.json").read_text(encoding="utf-8"))
FIXTURE = ROOT / "baseline.synthetic.json"


class ConstantProvider(BaseMetricsProvider):
    name = "constant"

    async def snapshot(self, clock):
        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics={"value": 42},
        )


class RegistryTests(unittest.TestCase):
    def test_custom_provider_can_be_built_without_core_changes(self) -> None:
        registry: Registry[BaseMetricsProvider] = Registry("provider")
        registry.register("constant", lambda cfg: ConstantProvider())
        provider = build_provider(
            {"provider": {"type": "constant"}},
            registry=registry,
        )
        self.assertIsInstance(provider, ConstantProvider)

    def test_existing_transport_alias_remains_supported(self) -> None:
        transport = build_transport(
            {"transport": {"type": "loopback_tcp", "host": "127.0.0.1", "port": 19050}}
        )
        self.assertEqual(transport.host, "127.0.0.1")
        self.assertEqual(transport.port, 19050)

    def test_clock_alias_is_registered(self) -> None:
        clock = build_clock({"clock": {"type": "fake", "start": "2030-01-01T00:00:00+00:00"}})
        self.assertIsInstance(clock, SimulatedClock)

    def test_hybrid_provider_is_registered(self) -> None:
        provider = build_provider(
            {"provider": {"type": "hybrid_network", "profile": str(FIXTURE)}}
        )
        self.assertIsInstance(provider, HybridSyntheticNetworkProvider)


class SchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_profile_snapshot_validates_against_runtime_schema(self) -> None:
        provider = build_provider({"profile": str(FIXTURE)})
        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        snapshot = await provider.snapshot(clock)
        envelope = snapshot.to_envelope(schema_version=1)
        jsonschema.validate(envelope, SCHEMA)
        self.assertIn("environment", envelope["metrics"])
        self.assertIn("software_batches", envelope["metrics"])
        self.assertIn("performance", envelope["metrics"])
        self.assertIn("process_snapshot", envelope["metrics"])

    async def test_live_provider_snapshot_validates_against_runtime_schema(self) -> None:
        provider = LiveSystemProvider(process_limit=5)
        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        await provider.start()
        try:
            snapshot = await provider.snapshot(clock)
        finally:
            await provider.stop()

        envelope = snapshot.to_envelope(schema_version=1)
        jsonschema.validate(envelope, SCHEMA)
        self.assertIn("cpu", envelope["metrics"])
        self.assertIn("memory", envelope["metrics"])
        self.assertIn("network_io", envelope["metrics"])
        self.assertLessEqual(len(envelope["metrics"]["process_snapshot"]), 5)

    async def test_hybrid_provider_reads_only_aggregate_network_state(self) -> None:
        provider = HybridSyntheticNetworkProvider(FIXTURE)
        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        first = SimpleNamespace(bytes_sent=1000, bytes_recv=2000)
        second = SimpleNamespace(bytes_sent=1300, bytes_recv=2600)

        forbidden = AssertionError("hybrid provider must not inspect host identity/system state")
        with (
            patch("psutil.net_io_counters", side_effect=[first, second]),
            patch("psutil.cpu_percent", side_effect=forbidden),
            patch("psutil.virtual_memory", side_effect=forbidden),
            patch("psutil.disk_usage", side_effect=forbidden),
            patch("psutil.disk_io_counters", side_effect=forbidden),
            patch("psutil.process_iter", side_effect=forbidden),
            patch("socket.gethostname", side_effect=forbidden),
        ):
            await provider.start()
            provider._last_time = 100.0
            with patch("telemetry.providers.time.monotonic", return_value=102.0):
                snapshot = await provider.snapshot(clock)

        envelope = snapshot.to_envelope(schema_version=1)
        jsonschema.validate(envelope, SCHEMA)
        metrics = envelope["metrics"]

        self.assertEqual(envelope["provider"], "hybrid-synthetic-network")
        self.assertEqual(metrics["cpu"]["source"], "synthetic")
        self.assertEqual(metrics["memory"]["source"], "synthetic")
        self.assertEqual(metrics["disk_io"]["source"], "synthetic")
        self.assertEqual(metrics["network_io"]["tx_bytes_per_second"], 150.0)
        self.assertEqual(metrics["network_io"]["rx_bytes_per_second"], 300.0)
        self.assertEqual(metrics["network_io"]["scope"], "aggregate")
        self.assertNotIn("host", metrics)
        self.assertNotIn("environment", metrics)


if __name__ == "__main__":
    unittest.main()
