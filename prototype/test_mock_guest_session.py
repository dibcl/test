import json
import os
import unittest

from mock_guest_session import BidirectionalGuestSession, SessionState, TestHostResponder as HostResponder
from mock_telemetry_agent import FrozenProfile, InMemoryTransport


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "lab", "mock-telemetry", "baseline.synthetic.json")


class MockGuestSessionTests(unittest.TestCase):
    def setUp(self):
        self.profile = FrozenProfile.load(PROFILE)
        self.transport = InMemoryTransport(responder=HostResponder())
        self.session = BidirectionalGuestSession(
            self.transport,
            self.profile.identity,
            self.profile.environment,
        )

    def test_full_handshake_and_monotonic_heartbeat(self):
        self.session.start("2030-01-01T00:00:00+00:00")
        self.assertEqual(self.session.state, SessionState.HEALTHY)
        self.assertEqual(self.session.acks.vm_info, 1)
        self.assertEqual(self.session.acks.mac, 1)
        self.assertEqual(self.session.acks.network, 1)
        self.assertEqual(self.session.acks.os, 1)
        self.assertEqual(self.session.acks.csap, 1)
        self.assertEqual(self.session.acks.ip_info, 1)
        for sequence in range(1, 6):
            self.session.heartbeat(f"2030-01-01T00:0{sequence}:00+00:00", sequence * 60)
            self.assertEqual(self.session.heartbeat_sequence, sequence)
        self.assertEqual(self.session.acks.heartbeat, 5)
        heartbeat = next(
            item for item in reversed(self.transport.messages)
            if item.source_module == 0x80000001 and item.int_msgid == 4002
        )
        self.assertEqual(heartbeat.payload["issysprep"], "0")
        self.assertNotIn("sequence", heartbeat.payload)
        self.assertNotIn("uptime_seconds", heartbeat.payload)
        version = next(item for item in self.transport.messages if item.int_msgid == 4004)
        self.assertEqual(version.payload, {
            "test_mode": True,
            "schema": "vmbooster_4004_v1",
            "msgtype": "4004",
            "vmid": "333333333333333333333333333333333333",
            "vmbooster": "TEST-V7.25.21",
            "PVDriver": "TEST-PV-1",
            "vdagent": "TEST-VDAGENT-1",
            "usbipc": "TEST-USBIPC-1",
            "media_redirect": "TEST-MEDIA-1",
            "vmagent": " ",
        })
        self.assertEqual(version.wire_payload, (
            b"{msgtype:'4004',vmid:'333333333333333333333333333333333333',"
            b"vmbooster:'TEST-V7.25.21',vmagent:' ',PVDriver:'TEST-PV-1',"
            b"vdagent:'TEST-VDAGENT-1',usbipc:'TEST-USBIPC-1',"
            b"media_redirect:'TEST-MEDIA-1'}"
        ))

    def test_renegotiation_returns_to_healthy(self):
        self.session.start("2030-01-01T00:00:00+00:00")
        self.session.renegotiate("2030-01-01T01:00:00+00:00")
        self.assertEqual(self.session.state, SessionState.HEALTHY)
        self.assertEqual(self.session.acks.vm_info, 2)

    def test_profile_without_test_mode_is_rejected(self):
        identity = dict(self.profile.identity)
        identity["test_mode"] = False
        with self.assertRaises(ValueError):
            BidirectionalGuestSession(self.transport, identity, self.profile.environment)

    def test_two_missed_heartbeat_acks_are_tolerated(self):
        transport = InMemoryTransport(responder=HostResponder(drop_heartbeat_acks=2))
        session = BidirectionalGuestSession(transport, self.profile.identity, self.profile.environment)
        session.start("2030-01-01T00:00:00+00:00")
        session.heartbeat("2030-01-01T00:00:30+00:00", 30)
        session.heartbeat("2030-01-01T00:01:00+00:00", 60)
        self.assertEqual(session.state, SessionState.HEALTHY)
        self.assertEqual(session.missed_heartbeat_acks, 2)
        session.heartbeat("2030-01-01T00:01:30+00:00", 90)
        self.assertEqual(session.state, SessionState.HEALTHY)
        self.assertEqual(session.missed_heartbeat_acks, 0)

    def test_third_consecutive_missed_ack_degrades(self):
        transport = InMemoryTransport(responder=HostResponder(drop_heartbeat_acks=3))
        session = BidirectionalGuestSession(transport, self.profile.identity, self.profile.environment)
        session.start("2030-01-01T00:00:00+00:00")
        for index in range(1, 4):
            session.heartbeat(f"2030-01-01T00:0{index}:00+00:00", index * 60)
        self.assertEqual(session.state, SessionState.DEGRADED)
        self.assertEqual(session.missed_heartbeat_acks, 3)

    def test_lock_state_correlates_8060_9053_and_8102c4(self):
        self.session.start("2030-01-01T00:00:00+00:00")
        self.session.set_lock_state("2030-01-01T00:01:00+00:00", True)
        by_id = {item.int_msgid: item for item in self.transport.messages}
        self.assertEqual(by_id[8060].payload["locked"], "1")
        self.assertEqual(by_id[0x8102C4].payload["raw_state"], 1)
        self.assertIn("session_state=locked", self.session.activity_state_event())
        self.assertIn("input_allowed=0", self.session.activity_state_event())
        self.assertFalse(self.session.activity_input_allowed())
        self.session.set_lock_state("2030-01-01T00:02:00+00:00", False)
        self.assertTrue(self.session.activity_input_allowed())

    def test_8047_client_quit_uses_confirmed_text_schema_and_updates_state(self):
        self.session.start("2030-01-01T00:00:00+00:00")
        result = self.session.emit_ice_client_quit("2030-01-01T00:01:00+00:00", 1493)
        event = next(item for item in self.transport.messages if item.int_msgid == 8047)
        self.assertTrue(event.payload["test_mode"])
        self.assertEqual(event.payload["schema"], "ice_inside_8047_v1")
        self.assertEqual(event.payload["msgid"], 1493)
        self.assertEqual(result.vmuuid, self.profile.identity["test_uuid"])
        self.assertFalse(self.session.ice_client_connected)
        self.assertFalse(event.payload["ack_expected"])
        self.assertEqual(event.wire_payload, (
            b"{msgtype:'8047',msgid:'1493',"
            b"vmuuid:'11111111-1111-4111-8111-111111111111'}"
        ))

    def test_8047_parse_path_updates_state_without_generating_ack(self):
        value = (
            "msgtype=8047;msgdata={msgtype:'8047',msgid:'7',"
            "vmuuid:'11111111-1111-4111-8111-111111111111'};"
        )
        response = self.session.handle_ice_inside_message(value)
        self.assertEqual(response["status"], "accepted")
        self.assertFalse(response["ack_generated"])
        self.assertEqual(self.session.last_client_quit.msgid, 7)


if __name__ == "__main__":
    unittest.main()
