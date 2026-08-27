from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from telemetry.clocks import RealClock, SimulatedClock
from telemetry.config import build_clock, build_settings, build_transport
from telemetry.runtime import ConfigFileWatcher, RuntimeState, TelemetryRuntime
from telemetry.transports import NetworkPolicy, TcpTransport, UdpTransport


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "baseline.synthetic.json"


class ClockHardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_simulated_clock_rejects_naive_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            SimulatedClock(datetime(2030, 1, 1))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            build_clock({"clock": {"type": "simulated", "start": "2030-01-01T00:00:00"}})

    async def test_clocks_reject_negative_and_nonfinite_sleep(self) -> None:
        for clock in (RealClock(), SimulatedClock()):
            for value in (-1.0, float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    await clock.sleep(value)


class StrictConfigTests(unittest.TestCase):
    def test_settings_reject_bool_fractional_and_nonfinite_values(self) -> None:
        bad_configs = (
            {"interval_seconds": True},
            {"interval_seconds": float("nan")},
            {"duration_seconds": float("inf")},
            {"schema_version": True},
            {"schema_version": 1.5},
            {"provider_health_timeout": float("nan")},
        )
        for config in bad_configs:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    build_settings(config)

    def test_transport_boolean_cannot_be_enabled_by_truthy_string(self) -> None:
        for kind in ("tcp", "udp"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, "allow_public.*boolean"):
                    build_transport({
                        "transport": {
                            "type": kind,
                            "host": "127.0.0.1",
                            "port": 19050,
                            "allow_public": "false",
                        }
                    })

    def test_live_tcp_timeout_must_be_finite_positive_number(self) -> None:
        for value in (True, 0, -1, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    TcpTransport("127.0.0.1", 19050, timeout=value)

    def test_reload_settings_require_real_boolean_and_finite_poll(self) -> None:
        base = {"profile": str(FIXTURE), "transport": {"type": "memory"}, "duration_seconds": 0}
        runtime = TelemetryRuntime({**base, "reload": {"enabled": "false"}})
        with self.assertRaisesRegex(ValueError, "reload.enabled.*boolean"):
            runtime._reload_settings()
        runtime = TelemetryRuntime({**base, "reload": {"enabled": True, "poll_seconds": float("nan")}})
        with self.assertRaisesRegex(ValueError, "poll_seconds.*finite"):
            runtime._reload_settings()
        with self.assertRaisesRegex(ValueError, "poll_seconds.*finite"):
            ConfigFileWatcher(TelemetryRuntime(base), "unused.json", float("inf"))


class NetworkHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_rejects_mixed_private_public_resolution(self) -> None:
        private = (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, "", ("192.168.1.10", 19051))
        public = (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, "", ("8.8.8.8", 19051))
        with patch("socket.getaddrinfo", return_value=[private, public]):
            with self.assertRaisesRegex(ValueError, "public address blocked"):
                await NetworkPolicy().resolve("mixed.invalid", 19051, socket.SOCK_DGRAM)

    async def test_udp_uses_exactly_one_resolution(self) -> None:
        info = (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, "", ("127.0.0.1", 19051))
        with patch("socket.getaddrinfo", return_value=[info]) as resolver:
            transport = UdpTransport("rebind.invalid", 19051)
            await transport.open()
            try:
                self.assertEqual(resolver.call_count, 1)
            finally:
                await transport.close()

    async def test_tcp_uses_validated_resolved_sockaddr_without_second_dns_lookup(self) -> None:
        accepted = asyncio.Event()

        async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            accepted.set()
            try:
                await reader.read(1)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(on_client, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        info = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))
        transport = TcpTransport("rebind.invalid", port, timeout=2.0)
        try:
            with patch("socket.getaddrinfo", return_value=[info]) as resolver:
                await transport.open()
                self.assertEqual(resolver.call_count, 1)
                await asyncio.wait_for(accepted.wait(), timeout=2.0)
        finally:
            await transport.close()
            server.close()
            await server.wait_closed()


class ConfigWatcherHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_malformed_config_is_reported_once_and_recovers(self) -> None:
        base = {
            "profile": str(FIXTURE),
            "transport": {"type": "memory"},
            "duration_seconds": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            original = json.dumps(base)
            path.write_text(original, encoding="utf-8")
            runtime = TelemetryRuntime(base)
            watcher = ConfigFileWatcher(runtime, path, poll_seconds=0.01)
            task = asyncio.create_task(watcher.run())
            try:
                await asyncio.sleep(0.03)
                path.write_text("{", encoding="utf-8")
                await asyncio.sleep(0.08)
                self.assertEqual(runtime.status.failed_reloads, 1)
                self.assertEqual(runtime.state, RuntimeState.DEGRADED)

                # A different malformed content is a new failure, but it should
                # still be counted only once while unchanged.
                path.write_text("[", encoding="utf-8")
                await asyncio.sleep(0.08)
                self.assertEqual(runtime.status.failed_reloads, 2)

                # Returning to the last known-good bytes clears only the active
                # reload failure without pretending a provider switch occurred.
                path.write_text(original, encoding="utf-8")
                await asyncio.sleep(0.05)
                self.assertEqual(runtime.status.failed_reloads, 2)
                self.assertEqual(runtime.state, RuntimeState.READY)
                self.assertIsNone(runtime.status.last_error)
            finally:
                await watcher.stop()
                await task

    async def test_watcher_stop_does_not_wait_for_long_poll_interval(self) -> None:
        base = {"profile": str(FIXTURE), "transport": {"type": "memory"}, "duration_seconds": 0}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            path.write_text(json.dumps(base), encoding="utf-8")
            watcher = ConfigFileWatcher(TelemetryRuntime(base), path, poll_seconds=60.0)
            task = asyncio.create_task(watcher.run())
            await asyncio.sleep(0)
            await watcher.stop()
            await asyncio.wait_for(task, timeout=1.0)


if __name__ == "__main__":
    unittest.main()
