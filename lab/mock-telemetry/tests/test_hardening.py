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
from telemetry.runtime import ConfigFileWatcher, RuntimeState, TelemetryRuntime
from telemetry.transports import NetworkPolicy, TcpTransport, UdpTransport


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "baseline.synthetic.json"


class ClockHardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_simulated_clock_rejects_naive_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            SimulatedClock(datetime(2030, 1, 1))

    async def test_clocks_reject_negative_and_nonfinite_sleep(self) -> None:
        for clock in (RealClock(), SimulatedClock()):
            for value in (-1.0, float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    await clock.sleep(value)


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


if __name__ == "__main__":
    unittest.main()
