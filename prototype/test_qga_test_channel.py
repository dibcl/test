import unittest
from datetime import datetime, timezone

from qga_test_channel import QgaJsonLineCodec, QgaPeriodicTestHarness, QgaTestError, QgaTestStateMachine


class QgaTestChannelTests(unittest.TestCase):
    def test_time_query_preserves_json_rpc_id(self):
        moment = datetime(2030, 1, 1, tzinfo=timezone.utc)
        qga = QgaTestStateMachine(clock=lambda: moment)
        response = qga.handle({"execute": "host-get-time", "id": 17})
        self.assertEqual(response, {
            "return": int(moment.timestamp() * 1_000_000_000),
            "id": 17,
        })
        self.assertEqual(qga.counters.time_requests, 1)

    def test_standard_alias_and_unknown_command(self):
        qga = QgaTestStateMachine()
        self.assertIn("return", qga.handle({"execute": "guest-get-time", "id": "a"}))
        response = qga.handle({"execute": "guest-exec", "id": "b"})
        self.assertEqual(response["error"]["class"], "CommandNotFound")
        self.assertEqual(qga.counters.errors, 1)

    def test_missing_or_non_string_execute_is_invalid(self):
        qga = QgaTestStateMachine()
        missing = qga.handle({"id": 0})
        typed = qga.handle({"execute": 123, "id": ["fixture"]})
        self.assertEqual(missing["error"]["class"], "InvalidParameter")
        self.assertEqual(typed["error"]["class"], "InvalidParameter")
        self.assertEqual(typed["id"], ["fixture"])
        self.assertEqual(qga.counters.invalid_requests, 2)

    def test_network_interfaces_are_static_fixtures_and_deep_copied(self):
        fixture = [{
            "name": "test0",
            "hardware-address": "02:00:00:00:00:01",
            "ip-addresses": [{
                "ip-address": "192.0.2.10",
                "ip-address-type": "ipv4",
                "prefix": 24,
            }],
        }]
        qga = QgaTestStateMachine(network_interfaces=fixture)
        fixture[0]["name"] = "mutated"
        response = qga.handle({"execute": "guest-network-get-interfaces", "id": "net-1"})
        self.assertEqual(response["id"], "net-1")
        self.assertEqual(response["return"][0]["name"], "test0")
        response["return"][0]["name"] = "caller-mutated"
        again = qga.handle({"execute": "guest-network-get-interfaces", "id": "net-2"})
        self.assertEqual(again["return"][0]["name"], "test0")

    def test_sync_commands_echo_fixture_id(self):
        qga = QgaTestStateMachine()
        response = qga.handle({
            "execute": "guest-sync-delimited",
            "arguments": {"id": 99},
            "id": "rpc-id",
        })
        self.assertEqual(response, {"return": 99, "id": "rpc-id"})
        invalid = qga.handle({"execute": "guest-sync", "arguments": {"id": "99"}, "id": 3})
        self.assertEqual(invalid["error"]["class"], "InvalidParameter")

    def test_json_line_codec_adds_delimited_sync_sentinel(self):
        request = {"execute": "guest-sync-delimited", "arguments": {"id": 5}, "id": 1}
        response = {"return": 5, "id": 1}
        wire = QgaJsonLineCodec.encode_response(request, response)
        self.assertEqual(wire[:1], b"\xff")
        self.assertTrue(wire.endswith(b"\n"))
        decoded = QgaJsonLineCodec.decode_request(b'\xff{"execute":"guest-get-time","id":2}\n')
        self.assertEqual(decoded["id"], 2)
        with self.assertRaises(QgaTestError):
            QgaJsonLineCodec.decode_request(b"[]\n")

    def test_periodic_harness_emits_every_ten_minutes(self):
        start = datetime(2030, 1, 1, tzinfo=timezone.utc)
        qga = QgaTestStateMachine(clock=lambda: start)
        harness = QgaPeriodicTestHarness(qga, start)
        harness.advance_to(start.replace(minute=19, second=59))
        self.assertEqual(len(harness.transcript), 1)
        harness.advance_to(start.replace(minute=20))
        self.assertEqual(len(harness.transcript), 2)
        self.assertEqual([pair[0]["id"] for pair in harness.transcript], [1, 2])


if __name__ == "__main__":
    unittest.main()
