from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from uuid import UUID


ROOT = Path(__file__).resolve().parents[3]
PROTOTYPE = ROOT / "prototype"
if str(PROTOTYPE) not in sys.path:
    sys.path.insert(0, str(PROTOTYPE))

from mock_mswitch_test_server import LoopbackMswitchTestServer

from message_adapters.model import ProtocolMessage
from message_adapters.mswitch_frame import (
    HEADER_SIZE,
    MAGIC,
    MswitchFrameEncoder,
    MswitchHeader,
    decode_serial_frame,
)
from telemetry.config import build_transport
from telemetry.mswitch_frame_transport import MswitchFrameTransport
from telemetry.runtime import TelemetryRuntime


TEST_UUID = "11111111-1111-4111-8111-111111111111"


def heartbeat() -> ProtocolMessage:
    return ProtocolMessage(
        int_msgid=4002,
        source_module=0x80000001,
        destination_module=0x80000000,
        emitted_at="2030-01-01 00:00:00.000",
        payload={
            "msgtype": "4002",
            "agentversion": "V7.25.21SP3pv",
            "vmid": "0" * 36,
            "agentstatus": "1",
            "computername": r"TEST;VM\ONE",
            "issysprep": "0",
        },
    )


class MswitchFrameEncoderTests(unittest.TestCase):
    def test_msgtype_header_fields_payload_length_and_serial_escaping(self) -> None:
        encoder = MswitchFrameEncoder(
            TEST_UUID,
            dst_type=1,
            msgtype_by_id={4002: 3},
        )
        frame = encoder.encode(heartbeat())
        self.assertEqual(frame[-1], 0x3B)

        raw = decode_serial_frame(frame)
        header = MswitchHeader.parse(raw)
        self.assertEqual(header.magic, MAGIC)
        self.assertEqual(header.version, 1)
        self.assertEqual(header.msgtype, 3)
        self.assertEqual(header.dst_type, 1)
        self.assertEqual(header.uuid, UUID(TEST_UUID).bytes)
        self.assertEqual(header.src_mod, 0x80000001)
        self.assertEqual(header.dst_mod, 0x80000000)
        self.assertEqual(header.int_msgid, 4002)
        self.assertEqual(header.data_len, 512)
        self.assertEqual(len(raw), HEADER_SIZE + 512)

        visible = raw[HEADER_SIZE:].rstrip(b"\x00")
        self.assertEqual(json.loads(visible), heartbeat().payload)
        self.assertGreater(len(frame), len(raw) + 1)

    def test_non_fixed_json_payload_uses_exact_utf8_length(self) -> None:
        message = ProtocolMessage(
            int_msgid=9050,
            source_module=0x80000011,
            destination_module=10,
            emitted_at="2030-01-01 00:00:00.000",
            payload={"source": 4, "environment": {"os": "%E7%89%88%E6%9C%AC"}},
        )
        raw = decode_serial_frame(MswitchFrameEncoder(TEST_UUID).encode(message))
        header = MswitchHeader.parse(raw)
        expected = json.dumps(
            message.payload, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(header.data_len, len(expected))
        self.assertEqual(raw[HEADER_SIZE:], expected)

    def test_4004_reuses_confirmed_plaintext_shape_and_fixed_size(self) -> None:
        message = ProtocolMessage(
            int_msgid=4004,
            source_module=0x80000001,
            destination_module=0x80000000,
            emitted_at="2030-01-01 00:00:29.000",
            payload={
                "msgtype": "4004",
                "vmid": "0" * 36,
                "vmbooster": "V7.25.21SP3pv",
                "vmagent": " ",
                "PVDriver": "3.18.34.723185c6",
                "vdagent": "",
                "usbipc": "",
                "media_redirect": "",
            },
        )
        raw = decode_serial_frame(MswitchFrameEncoder(TEST_UUID).encode(message))
        header = MswitchHeader.parse(raw)
        self.assertEqual(header.data_len, 512)
        self.assertTrue(raw[HEADER_SIZE:].startswith(b"{msgtype:'4004'"))

    def test_transport_registry_builds_both_output_modes(self) -> None:
        for mode in ("json_debug", "mswitch"):
            transport = build_transport({
                "transport": {
                    "type": "mswitch_frame",
                    "host": "127.0.0.1",
                    "port": 19050,
                    "uuid": TEST_UUID,
                    "mode": mode,
                }
            })
            self.assertIsInstance(transport, MswitchFrameTransport)
            self.assertEqual(transport.mode, mode)


class _Writer:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, value: bytes) -> None:
        self.data.extend(value)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class MswitchFrameTransportModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_debug_preserves_current_protocol_message_json(self) -> None:
        transport = MswitchFrameTransport(
            "127.0.0.1", 19050, TEST_UUID, mode="json_debug"
        )
        writer = _Writer()
        transport.writer = writer
        await transport.send(heartbeat().to_dict())
        self.assertEqual(json.loads(writer.data), heartbeat().to_dict())

    async def test_mswitch_mode_outputs_binary_frame(self) -> None:
        transport = MswitchFrameTransport(
            "127.0.0.1",
            19050,
            TEST_UUID,
            mode="mswitch",
            msgtype_by_id={"4002": 3},
            ack_by_request={},
        )
        writer = _Writer()
        transport.writer = writer
        await transport.send(heartbeat().to_dict())
        raw = decode_serial_frame(bytes(writer.data))
        self.assertEqual(MswitchHeader.parse(raw).msgtype, 3)

    async def test_transport_exchanges_4002_for_4100_with_existing_responder(self) -> None:
        with LoopbackMswitchTestServer(TEST_UUID) as server:
            transport = MswitchFrameTransport(
                "127.0.0.1", server.port, TEST_UUID, mode="mswitch"
            )
            await transport.open()
            await transport.send(heartbeat().to_dict())
            await transport.close()

        self.assertEqual([item.int_msgid for item in server.messages], [4002])
        self.assertEqual(
            [item.int_msgid for item in transport.acknowledgements],
            [4100],
        )

    async def test_runtime_frame_mock_host_ack_closes_the_full_loop(self) -> None:
        config_path = Path(__file__).parents[1] / "config.windows-validation.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["clock"] = {
            "type": "simulated",
            "start": "2030-01-01T00:00:00+00:00",
        }
        config["duration_seconds"] = 9
        config["provider"] = dict(config["provider"])
        config["provider"].pop("state_path", None)

        with LoopbackMswitchTestServer(TEST_UUID) as server:
            config["transport"] = {
                "type": "mswitch_frame",
                "host": "127.0.0.1",
                "port": server.port,
                "uuid": TEST_UUID,
                "mode": "mswitch",
                "ack_by_request": {"4002": 4100},
            }
            runtime = TelemetryRuntime(config)
            transport = runtime.agent._transport
            await runtime.run()

        self.assertEqual(runtime.status.state.value, "stopped")
        self.assertEqual(
            [item.int_msgid for item in server.messages],
            [9050, 9054, 9054, 9054, 4002],
        )
        self.assertEqual(
            [item.int_msgid for item in transport.acknowledgements],
            [4100],
        )


if __name__ == "__main__":
    unittest.main()
