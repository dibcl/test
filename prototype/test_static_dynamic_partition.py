from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import unittest

from mock_guest_session import BidirectionalGuestSession, TestHostResponder as HostResponder
from mock_telemetry_agent import FrozenProfile, InMemoryTransport, MockTelemetryAgent
from static_payloads import StaticPayloadBuilder


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "lab", "mock-telemetry", "baseline.synthetic.json")


class StaticDynamicPartitionTests(unittest.TestCase):
    def test_static_builder_is_repeatable_and_does_not_mutate_profile(self):
        profile = FrozenProfile.load(PROFILE)
        before_environment = json.loads(json.dumps(profile.environment))
        before_software = json.loads(json.dumps(profile.software_batches))
        builder = StaticPayloadBuilder.from_profile(profile)
        first_environment = builder.environment_payload("2030-01-01T00:00:00.000+00:00")
        second_environment = builder.environment_payload("2030-01-01T00:00:00.000+00:00")
        self.assertEqual(first_environment, second_environment)
        self.assertEqual(
            builder.software_payloads("2030-01-01T00:00:00.000+00:00"),
            builder.software_payloads("2030-01-01T00:00:00.000+00:00"),
        )
        self.assertEqual(profile.environment, before_environment)
        self.assertEqual(json.loads(json.dumps(profile.software_batches)), before_software)

    def test_changing_dynamic_seed_only_changes_9051_metrics(self):
        base = FrozenProfile.load(PROFILE)

        def run(seed):
            dynamics = dict(base.dynamics)
            dynamics["seed"] = seed
            profile = replace(base, dynamics=dynamics)
            transport = InMemoryTransport(responder=HostResponder())
            session = BidirectionalGuestSession(transport, profile.identity, profile.environment)
            agent = MockTelemetryAgent(
                profile,
                transport,
                start_time=datetime(2030, 1, 1, tzinfo=timezone.utc),
                control_session=session,
            )
            agent.start()
            agent._emit_qoe_window()
            return transport.messages

        first = run(100)
        second = run(200)

        def payloads(messages, msgid):
            return [item.payload for item in messages if item.int_msgid == msgid]

        for msgid in (4004, 9050, 9052, 9053, 9054):
            self.assertEqual(payloads(first, msgid), payloads(second, msgid), msgid)
        self.assertNotEqual(payloads(first, 9051), payloads(second, 9051))

        process = payloads(first, 9052)[0]
        self.assertEqual(process["process"], list(base.process_snapshot["process"]))
        activity = payloads(first, 9053)[0]
        self.assertEqual(activity["logdatas"][0]["log"], base.activity_events[0])


if __name__ == "__main__":
    unittest.main()
