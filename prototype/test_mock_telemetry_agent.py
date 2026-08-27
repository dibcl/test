import os
import time
import unittest
from datetime import datetime

from mock_telemetry_agent import (
    FaultPlan,
    FrozenProfile,
    InMemoryTransport,
    LoopbackJsonTcpTransport,
    MockTelemetryAgent,
    MockTelemetryError,
    build_transport,
)
from mock_telemetry_test_server import LoopbackTestServer
from mock_guest_session import BidirectionalGuestSession, TestHostResponder as HostResponder


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "lab", "mock-telemetry", "baseline.synthetic.json")


class MockTelemetryAgentTests(unittest.TestCase):
    def test_complete_static_schedule(self):
        profile = FrozenProfile.load(PROFILE)
        transport = InMemoryTransport()
        agent = MockTelemetryAgent(profile, transport)
        agent.emit_startup()
        agent.run_for(600)
        ids = [item.int_msgid for item in transport.messages]
        self.assertEqual(ids.count(9050), 1)
        self.assertEqual(ids.count(9054), 3)
        self.assertEqual(ids.count(9055), 1)
        self.assertEqual(ids.count(9060), 1)
        self.assertEqual(ids.count(4002), 21)
        for msgid in (9051, 9052, 9053, 9056):
            self.assertEqual(ids.count(msgid), 2)

    def test_profile_is_frozen_and_contains_only_synthetic_values(self):
        profile = FrozenProfile.load(PROFILE)
        os.environ["COMPUTERNAME"] = "SHOULD-NOT-BE-COLLECTED"
        transport = InMemoryTransport()
        agent = MockTelemetryAgent(profile, transport)
        agent.emit_startup()
        payload = next(item.payload for item in transport.messages if item.int_msgid == 9050)
        self.assertEqual(payload["source"], 4)
        self.assertEqual(payload["hostid"], "0" * 36)
        self.assertEqual(payload["environment"]["computername"], "TEST-WIN10")
        self.assertNotIn("SHOULD-NOT-BE-COLLECTED", str(payload))
        self.assertNotIn("gateway", payload["environment"])
        self.assertNotIn("netmask", payload["environment"])
        self.assertNotIn("dns", payload["environment"])
        self.assertNotIn("dhcp", payload["environment"])
        self.assertIn("Microsoft+Windows", payload["environment"]["os"])
        self.assertEqual(payload["environment"]["targetversion"], "")
        inventory = next(item.payload for item in transport.messages if item.int_msgid == 9054)
        self.assertIn("+", inventory["softwares"][0]["name"])

    def test_seeded_timer_jitter_stays_within_profile_bounds(self):
        profile = FrozenProfile.load(PROFILE)
        transport = InMemoryTransport()
        agent = MockTelemetryAgent(profile, transport)
        agent.emit_startup()
        agent.run_for(1800)
        heartbeat_times = [datetime.fromisoformat(item.emitted_at) for item in transport.messages if item.int_msgid == 4002]
        heartbeat_deltas = [(right - left).total_seconds() for left, right in zip(heartbeat_times, heartbeat_times[1:])]
        self.assertTrue(all(28.0 <= item <= 31.0 for item in heartbeat_deltas))
        qoe_times = [datetime.fromisoformat(item.emitted_at) for item in transport.messages if item.int_msgid == 9051]
        qoe_deltas = [(right - left).total_seconds() for left, right in zip(qoe_times, qoe_times[1:])]
        self.assertTrue(all(298.0 <= item <= 303.0 for item in qoe_deltas))

    def test_fault_plan_drops_duplicates_and_stales(self):
        profile = FrozenProfile.load(PROFILE)
        transport = InMemoryTransport()
        faults = FaultPlan(
            drop_ids=frozenset({9052}),
            duplicate_ids=frozenset({9054}),
            stale_timestamp_ids=frozenset({9053}),
        )
        agent = MockTelemetryAgent(profile, transport, faults=faults)
        agent.emit_startup()
        agent.run_for(300)
        ids = [item.int_msgid for item in transport.messages]
        self.assertEqual(ids.count(9054), 6)
        self.assertNotIn(9052, ids)
        event = next(item for item in transport.messages if item.int_msgid == 9053)
        self.assertTrue(event.emitted_at.startswith("2029-12-31"))

    def test_real_loopback_tcp_end_to_end(self):
        profile = FrozenProfile.load(PROFILE)
        with LoopbackTestServer() as server:
            transport = LoopbackJsonTcpTransport("127.0.0.1", server.port)
            agent = MockTelemetryAgent(profile, transport)
            agent.emit_startup()
            agent.run_for(30)
            for _ in range(50):
                if sum(item["int_msgid"] == 4002 for item in server.messages) >= 2:
                    break
                time.sleep(0.01)
            agent.close()
        ids = [item["int_msgid"] for item in server.messages]
        self.assertIn(9050, ids)
        self.assertEqual(ids.count(9054), 3)
        self.assertEqual(ids.count(4002), 2)

    def test_tcp_provider_rejects_non_loopback(self):
        with self.assertRaises(MockTelemetryError):
            LoopbackJsonTcpTransport("192.0.2.1", 19050, timeout=0.01)

    def test_external_override_was_removed(self):
        with self.assertRaises(MockTelemetryError):
            build_transport({
                "type": "loopback_tcp",
                "host": "127.0.0.1",
                "port": 19050,
                "allow_external": True,
            })

    def test_9053_contains_correlated_session_state(self):
        profile = FrozenProfile.load(PROFILE)
        transport = InMemoryTransport(responder=HostResponder())
        session = BidirectionalGuestSession(transport, profile.identity, profile.environment)
        agent = MockTelemetryAgent(profile, transport, control_session=session)
        agent.start()
        session.set_lock_state(agent._stamp(), True)
        agent.run_for(303)
        activity = next(item.payload for item in reversed(transport.messages) if item.int_msgid == 9053)
        self.assertTrue(any("session_state=locked" in row["log"] for row in activity["logdatas"]))
        self.assertTrue(any("synthetic active interval=5min" in row["log"] for row in activity["logdatas"]))
        self.assertFalse(any("keyboard_delta=" in row["log"] for row in activity["logdatas"]))

    def test_dynamic_9051_uses_synthetic_network_signal(self):
        profile = FrozenProfile.load(PROFILE)
        transport = InMemoryTransport()
        agent = MockTelemetryAgent(profile, transport)
        agent.run_for(303)
        payload = next(item.payload for item in transport.messages if item.int_msgid == 9051)
        rows = [sample["network"][0]["data"] for sample in payload["performance"]]
        self.assertTrue(all(row.startswith("02-00-00-00-00-10|") for row in rows))
        self.assertGreater(len(set(rows)), 1)


if __name__ == "__main__":
    unittest.main()
