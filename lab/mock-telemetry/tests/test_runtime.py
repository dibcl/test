from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telemetry.agent import AgentSettings, TelemetryAgent
from telemetry.clocks import SimulatedClock
from telemetry.config import build_provider, build_settings, build_transport
from telemetry.model import ProviderHealth, TelemetrySnapshot
from telemetry.providers import BaseMetricsProvider, FrozenProfileProvider, SyntheticMetricsProvider
from telemetry.runtime import ConfigFileWatcher, RuntimeState, TelemetryRuntime
from telemetry.transports import FileDumpTransport, MemoryTransport


FIXTURE = Path(__file__).resolve().parents[1] / "baseline.synthetic.json"


class LifecycleProvider(BaseMetricsProvider):
    def __init__(self, name: str, *, healthy: bool = True) -> None:
        self.name = name
        self.healthy = healthy
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(self.healthy, "ready" if self.healthy else "unhealthy fixture")

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
        with self.assertRaises(ValueError):
            build_settings({"provider_health_timeout": 0})


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

    async def test_provider_switch_is_probed_when_agent_is_idle(self) -> None:
        old = LifecycleProvider("old")
        new = LifecycleProvider("new")
        agent = TelemetryAgent(
            provider=old,
            transport=MemoryTransport(),
            clock=SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc)),
            settings=AgentSettings(interval_seconds=1, duration_seconds=0),
        )

        result = await agent.set_provider(new)

        self.assertIs(agent.provider, new)
        self.assertTrue(result.health.healthy)
        self.assertEqual(new.started, 1)
        self.assertEqual(new.stopped, 1)
        self.assertEqual(old.stopped, 0)

    async def test_failed_provider_switch_rolls_back_without_touching_old_provider(self) -> None:
        old = LifecycleProvider("old")
        bad = LifecycleProvider("bad", healthy=False)
        agent = TelemetryAgent(
            provider=old,
            transport=MemoryTransport(),
            clock=SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc)),
            settings=AgentSettings(interval_seconds=1, duration_seconds=0),
        )

        with self.assertRaisesRegex(RuntimeError, "unhealthy fixture"):
            await agent.set_provider(bad)

        self.assertIs(agent.provider, old)
        self.assertEqual(old.stopped, 0)
        self.assertEqual(bad.started, 1)
        self.assertEqual(bad.stopped, 1)

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

        result = await runtime.switch_provider(
            {"type": "synthetic", "profile": str(FIXTURE)}
        )

        self.assertTrue(result.changed)
        self.assertIsInstance(runtime.agent.provider, SyntheticMetricsProvider)
        self.assertEqual(runtime.config["provider"]["type"], "synthetic")
        self.assertNotIn("profile", runtime.config)
        self.assertEqual(runtime.status.generation, 1)
        self.assertEqual(runtime.state, RuntimeState.READY)

    async def test_apply_config_marks_non_provider_changes_restart_required(self) -> None:
        runtime = TelemetryRuntime(
            {
                "profile": str(FIXTURE),
                "transport": {"type": "memory"},
                "interval_seconds": 1,
                "duration_seconds": 0,
            }
        )
        await runtime.apply_config(
            {
                "profile": str(FIXTURE),
                "transport": {"type": "memory"},
                "interval_seconds": 2,
                "duration_seconds": 0,
            }
        )
        self.assertTrue(runtime.status.restart_required)
        self.assertEqual(runtime.status.successful_reloads, 1)
        self.assertEqual(runtime.config.get("interval_seconds"), 1)
        self.assertEqual(runtime.desired_config.get("interval_seconds"), 2)

    async def test_config_file_watcher_switches_provider_after_atomic_config_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "runtime.json"
            initial = {
                "profile": str(FIXTURE),
                "transport": {"type": "memory"},
                "duration_seconds": 0,
            }
            config_path.write_text(json.dumps(initial), encoding="utf-8")
            runtime = TelemetryRuntime.from_file(config_path)
            watcher = ConfigFileWatcher(runtime, config_path, poll_seconds=0.01)
            task = asyncio.create_task(watcher.run())
            await asyncio.sleep(0.03)

            changed = {
                "provider": {"type": "synthetic", "profile": str(FIXTURE)},
                "transport": {"type": "memory"},
                "duration_seconds": 0,
            }
            config_path.write_text(json.dumps(changed), encoding="utf-8")

            for _ in range(100):
                if isinstance(runtime.agent.provider, SyntheticMetricsProvider):
                    break
                await asyncio.sleep(0.01)

            await watcher.stop()
            await task
            self.assertIsInstance(runtime.agent.provider, SyntheticMetricsProvider)
            self.assertGreaterEqual(runtime.status.successful_reloads, 1)
            self.assertEqual(runtime.status.failed_reloads, 0)

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
