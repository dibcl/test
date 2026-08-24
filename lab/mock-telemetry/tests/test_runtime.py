from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telemetry.agent import AgentSettings, TelemetryAgent
from telemetry.clocks import SimulatedClock
from telemetry.config import build_provider, build_settings, build_transport
from telemetry.model import TelemetrySnapshot
from telemetry.providers import BaseMetricsProvider, FrozenProfileProvider, SyntheticMetricsProvider
from telemetry.runtime import TelemetryRuntime
from telemetry.transports import FileDumpTransport, MemoryTransport


FIXTURE = Path(__file__).resolve().parents[1] / "baseline.synthetic.json"


class LifecycleProvider(BaseMetricsProvider):
    def __init__(self, name: str) -> None:
        self.name = name
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def snapshot(self, clock) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics={"provider": self.name},
        )


class ProviderCompatibilityTests(unittest.TestCase):
    def test_legacy_profile_config_maps_to_frozen_provider(self) -> None:
        provider = build_provider({"profile": str(FIXTURE)})
        self.assertIsInstance(provider, FrozenProfileProvider)

    def test_explicit_synthetic_provider(self) -> None:
        provider = build_provider(
            {"provider": {"type": "synthetic", "profile": str(FIXTURE)}}
        )
        self.assertIsInstance(provider, SyntheticMetricsProvider)

    def test_legacy_loopback_name_maps_to_tcp_transport(self) -> None:
        transport = build_transport(
            {
                "transport": {
                    "type": "loopback_tcp",
                    "host": "127.0.0.1",
                    "port": 19050,
                }
            }
        )
        self.assertEqual(transport.host, "127.0.0.1")
        self.assertEqual(transport.port, 19050)

    def test_legacy_test_mode_is_not_a_runtime_gate(self) -> None:
        provider = build_provider({"profile": str(FIXTURE)})
        self.assertTrue(provider.profile["identity"]["test_mode"])

    def test_invalid_agent_settings_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_settings({"interval_seconds": 0})
        with self.assertRaises(ValueError):
            build_settings({"duration_seconds": -1})
        with self.assertRaises(ValueError):
            build_settings({"schema_version": 0})


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulated_clock_runs_without_wall_clock_delay(self) -> None:
        provider = FrozenProfileProvider(FIXTURE)
        transport = MemoryTransport()
        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        agent = TelemetryAgent(
            provider=provider,
            transport=transport,
            clock=clock,
            settings=AgentSettings(interval_seconds=5, duration_seconds=10),
        )
        await agent.run()
        self.assertEqual(len(transport.messages), 3)
        self.assertEqual(transport.messages[0]["provider"], "frozen-profile")
        self.assertIn("software_batches", transport.messages[0]["metrics"])
        self.assertIn("activity_events", transport.messages[0]["metrics"])

    async def test_provider_can_be_switched_at_runtime_boundary(self) -> None:
        old = LifecycleProvider("old")
        new = LifecycleProvider("new")
        agent = TelemetryAgent(
            provider=old,
            transport=MemoryTransport(),
            clock=SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc)),
            settings=AgentSettings(interval_seconds=1, duration_seconds=0),
        )

        await agent.set_provider(new)

        self.assertIs(agent.provider, new)
        self.assertEqual(new.started, 1)
        self.assertEqual(old.stopped, 1)

    async def test_runtime_switches_provider_from_config(self) -> None:
        runtime = TelemetryRuntime(
            {
                "profile": str(FIXTURE),
                "transport": {"type": "memory"},
                "clock": {"type": "simulated", "start": "2030-01-01T00:00:00+00:00"},
                "duration_seconds": 0,
            }
        )
        self.assertIsInstance(runtime.agent.provider, FrozenProfileProvider)

        await runtime.switch_provider(
            {"type": "synthetic", "profile": str(FIXTURE)}
        )

        self.assertIsInstance(runtime.agent.provider, SyntheticMetricsProvider)
        self.assertEqual(runtime.config["provider"]["type"], "synthetic")
        self.assertNotIn("profile", runtime.config)

    async def test_file_dump_writes_ndjson(self) -> None:
        provider = FrozenProfileProvider(FIXTURE)
        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "telemetry.jsonl"
            transport = FileDumpTransport(output)
            agent = TelemetryAgent(
                provider=provider,
                transport=transport,
                clock=clock,
                settings=AgentSettings(interval_seconds=1, duration_seconds=0),
            )
            await agent.run()
            rows = output.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            decoded = json.loads(rows[0])
            self.assertIn("metrics", decoded)
            self.assertEqual(decoded["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
