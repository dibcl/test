import json
import os
import tempfile
import unittest

from guest_management_simulator import (
    CurrentBaseline,
    FakeHost,
    OfflineGuestSimulator,
    SimulatorState,
    load_current_baseline,
)
from mswitch_protocol import ProtocolError, SerialFrameDecoder, encode_serial_frame


def baseline():
    return CurrentBaseline(
        source_uuid="00000000-0000-0000-0000-000000000001",
        environment={
            "computername": "TEST-GUEST",
            "os": "Microsoft Windows [Version 10.0.19044]",
            "bit": "64",
            "mac": "00-00-00-00-00-01",
            "ip": "192.0.2.10",
            "version": "TEST-1",
        },
        software=({"name": "Synthetic Package", "type": "1"},),
    )


class GuestManagementSimulatorTests(unittest.TestCase):
    def test_complete_startup_and_heartbeat(self):
        current = baseline()
        host = FakeHost(current.offline_uuid)
        guest = OfflineGuestSimulator(current, host)
        guest.run_startup()
        self.assertEqual(guest.state, SimulatorState.HEALTHY)
        guest.heartbeat()
        guest.heartbeat()
        self.assertEqual(guest.heartbeat_count, 2)

    def test_dynamic_ip_token_is_correlated(self):
        current = baseline()
        guest = OfflineGuestSimulator(current, FakeHost(current.offline_uuid))
        guest.run_startup()
        guest.request_ip_info(521910635)

    def test_state_machine_rejects_out_of_order_heartbeat(self):
        current = baseline()
        guest = OfflineGuestSimulator(current, FakeHost(current.offline_uuid))
        with self.assertRaises(ProtocolError):
            guest.heartbeat()

    def test_every_emitted_message_survives_serial_framing(self):
        current = baseline()
        host = FakeHost(current.offline_uuid)
        guest = OfflineGuestSimulator(current, host)
        guest.run_startup()
        guest.heartbeat()
        guest.request_ip_info(7)
        decoder = SerialFrameDecoder()
        for message in host.received:
            raw = message.to_bytes()
            self.assertEqual(decoder.feed(encode_serial_frame(raw)), [raw])

    def test_current_log_loader_uses_latest_boot_and_decodes_values(self):
        env_old = {
            "uuid": "old",
            "environment": {"computername": "OLD", "os": "Old"},
        }
        env_new = {
            "uuid": "new",
            "environment": {
                "computername": "TEST%2DGUEST",
                "os": "Microsoft+Windows",
                "bit": "64",
            },
        }
        inventory = {
            "softwares": [{"name": "Synthetic+Package", "type": "1"}]
        }
        performance = {"performance": []}
        lines = [
            f"x int_msgid=9050, x msg={json.dumps(env_old)}\n",
            f"x int_msgid=9050, x msg={json.dumps(env_new)}\n",
            f"x int_msgid=9054, x msg={json.dumps(inventory)}\n",
            f"x int_msgid=9051, x msg={json.dumps(performance)}\n",
        ]
        path = ""
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                path = handle.name
                handle.writelines(lines)
            loaded = load_current_baseline(path)
        finally:
            if path:
                os.unlink(path)
        self.assertEqual(loaded.source_uuid, "new")
        self.assertEqual(loaded.environment["computername"], "TEST-GUEST")
        self.assertEqual(loaded.software[0]["name"], "Synthetic Package")
        self.assertNotIn("new", loaded.public_summary().values())


if __name__ == "__main__":
    unittest.main()

