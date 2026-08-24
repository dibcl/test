from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from telemetry.clocks import SimulatedClock
from telemetry.config import build_clock, build_provider, build_transport
from telemetry.model import TelemetrySnapshot
from telemetry.providers import BaseMetricsProvider, LiveSystemProvider
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


if __name__ == "__main__":
    unittest.main()
