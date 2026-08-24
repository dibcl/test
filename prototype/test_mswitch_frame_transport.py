import os
import struct
import unittest

from mock_guest_session import BidirectionalGuestSession
from mock_telemetry_agent import Envelope, FrozenProfile, MockTelemetryAgent, MockTelemetryError
from mswitch_frame_transport import MswitchFrameEncoder
from mswitch_frame_transport import (
    LocalStreamMswitchTransport,
    LocalTestNamedPipeMswitchTransport,
    LoopbackMswitchTcpTransport,
    LoopbackMswitchUnixTransport,
)
from mock_mswitch_test_server import LoopbackMswitchTestServer
from mswitch_protocol import MAGIC, SerialFrameDecoder, parse_message


TEST_UUID = "11111111-1111-4111-8111-111111111111"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "lab", "mock-telemetry", "baseline.synthetic.json")


class MswitchFrameTransportTests(unittest.TestCase):
    def test_encoder_uses_header_offsets_and_serial_escaping(self):
        encoder = MswitchFrameEncoder(
            TEST_UUID,
            dst_type=7,
            msgtype_by_id={4002: 3},
            test_mode=True,
        )
        envelope = Envelope(
            4002,
            0x80000001,
            0x80000000,
            "2030-01-01T00:00:00+00:00",
            {"text": r"a;b\\c"},
        )
        wire = encoder.encode(envelope)
        self.assertEqual(wire[-1], 0x3B)
        frames = SerialFrameDecoder().feed(wire)
        self.assertEqual(len(frames), 1)
        message = parse_message(frames[0])
        self.assertEqual(struct.unpack_from("<I", message.raw_header, 0)[0], MAGIC)
        self.assertEqual(message.dst_type, 7)
        self.assertEqual(message.msgtype, 3)
        self.assertEqual(message.src_mod, 0x80000001)
        self.assertEqual(message.dst_mod, 0x80000000)
        self.assertEqual(message.int_msgid, 4002)
        decoded = encoder.decode(frames[0])
        self.assertEqual(decoded.payload, envelope.payload)

    def test_8102c4_is_exactly_one_payload_byte(self):
        encoder = MswitchFrameEncoder(TEST_UUID, test_mode=True)
        wire = encoder.encode(Envelope(
            0x8102C4,
            0x80000001,
            6,
            "2030-01-01T00:00:00+00:00",
            {"test_mode": True, "raw_state": 1},
        ))
        frame = SerialFrameDecoder().feed(wire)[0]
        message = parse_message(frame)
        self.assertEqual(message.payload, b"\x01")
        self.assertEqual(encoder.decode(frame).payload["raw_state"], 1)

    def test_test_metadata_is_not_serialized_into_json_wire_payload(self):
        encoder = MswitchFrameEncoder(TEST_UUID, test_mode=True)
        wire = encoder.encode(Envelope(
            4002,
            0x80000001,
            0x80000000,
            "2030-01-01T00:00:00+00:00",
            {
                "test_mode": True,
                "schema": "fixture",
                "msgtype": "4002",
                "agentversion": "TEST-V1",
                "vmid": "fixture",
                "agentstatus": "1",
                "computername": "SYNTHETIC",
                "issysprep": "0",
            },
        ))
        message = parse_message(SerialFrameDecoder().feed(wire)[0])
        expected = (
            b'{"msgtype":"4002","agentversion":"TEST-V1","vmid":"fixture",'
            b'"agentstatus":"1","computername":"SYNTHETIC","issysprep":"0"}'
        )
        self.assertEqual(len(message.payload), 512)
        self.assertEqual(message.payload[:len(expected)], expected)
        self.assertEqual(message.payload[len(expected):], b"\x00" * (512 - len(expected)))
        self.assertNotIn(b"test_mode", message.payload)

    def test_encoder_requires_explicit_test_mode(self):
        with self.assertRaises(MockTelemetryError):
            MswitchFrameEncoder(TEST_UUID)

    def test_loopback_transport_rejects_non_loopback_destination(self):
        with self.assertRaises(MockTelemetryError):
            LoopbackMswitchTcpTransport(
                "192.0.2.1",
                19050,
                TEST_UUID,
                timeout=0.01,
                test_mode=True,
            )

    def test_local_stream_handles_one_byte_writes_and_fragmented_response(self):
        response_encoder = MswitchFrameEncoder(TEST_UUID, test_mode=True)
        response_wire = response_encoder.encode(Envelope(
            4100,
            0x80000000,
            0x80000001,
            "2030-01-01T00:00:00+00:00",
            {"test_mode": True, "sequence": 7},
        ))

        class OneByteStream:
            def __init__(self):
                self.written = bytearray()
                self.reads = [bytes((value,)) for value in response_wire]
                self.closed = False

            def write(self, value):
                self.written.extend(value[:1])
                return 1

            def read(self, _size):
                return self.reads.pop(0) if self.reads else b""

            def close(self):
                self.closed = True

        stream = OneByteStream()
        transport = LocalStreamMswitchTransport(stream, TEST_UUID, test_mode=True)
        responses = transport.exchange(Envelope(
            4002,
            0x80000001,
            0x80000000,
            "2030-01-01T00:00:00+00:00",
            {"test_mode": True, "sequence": 7},
        ))
        self.assertEqual([item.int_msgid for item in responses], [4100])
        self.assertGreater(len(stream.written), 0)
        transport.close()
        self.assertTrue(stream.closed)

    def test_local_stream_decodes_multiple_sticky_frames(self):
        encoder = MswitchFrameEncoder(TEST_UUID, test_mode=True)
        frames = b"".join(encoder.encode(Envelope(
            msgid,
            0x80000000,
            0x80000001,
            "2030-01-01T00:00:00+00:00",
            {"test_mode": True, "fixture": msgid},
        )) for msgid in (4100, 8009))

        class StickyStream:
            def __init__(self):
                self.response = frames

            def write(self, value):
                return len(value)

            def read(self, _size):
                value, self.response = self.response, b""
                return value

            def close(self):
                return None

        transport = LocalStreamMswitchTransport(StickyStream(), TEST_UUID, test_mode=True)
        responses = transport.exchange(Envelope(
            4002,
            0x80000001,
            0x80000000,
            "2030-01-01T00:00:00+00:00",
            {"test_mode": True},
        ))
        self.assertEqual([item.int_msgid for item in responses], [4100, 8009])

    def test_disconnect_discards_incomplete_frame_before_reuse(self):
        encoder = MswitchFrameEncoder(TEST_UUID, test_mode=True)
        complete = encoder.encode(Envelope(
            4100,
            0x80000000,
            0x80000001,
            "2030-01-01T00:00:00+00:00",
            {"test_mode": True},
        ))

        class ReconnectableStream:
            def __init__(self):
                self.reads = [complete[:20], b""]

            def write(self, value):
                return len(value)

            def read(self, _size):
                return self.reads.pop(0)

            def close(self):
                return None

        stream = ReconnectableStream()
        transport = LocalStreamMswitchTransport(stream, TEST_UUID, test_mode=True)
        request = Envelope(
            4002,
            0x80000001,
            0x80000000,
            "2030-01-01T00:00:00+00:00",
            {"test_mode": True},
        )
        with self.assertRaises(MockTelemetryError):
            transport.exchange(request)
        stream.reads = [complete]
        self.assertEqual(transport.exchange(request)[0].int_msgid, 4100)

    def test_local_pipe_providers_reject_non_test_names(self):
        with self.assertRaises(MockTelemetryError):
            LoopbackMswitchUnixTransport("ordinary.sock", TEST_UUID, test_mode=True)
        with self.assertRaises(MockTelemetryError):
            LocalTestNamedPipeMswitchTransport("bad\\name", TEST_UUID, test_mode=True)

    def test_loopback_binary_exchange_with_fake_host(self):
        with LoopbackMswitchTestServer(TEST_UUID) as server:
            transport = LoopbackMswitchTcpTransport(
                "127.0.0.1",
                server.port,
                TEST_UUID,
                test_mode=True,
            )
            responses = transport.exchange(Envelope(
                4002,
                0x80000001,
                0x80000000,
                "2030-01-01T00:00:00+00:00",
                {"test_mode": True, "sequence": 9},
            ))
            transport.close()
        self.assertEqual([item.int_msgid for item in responses], [4100])
        self.assertEqual(responses[0].payload["sequence"], 9)
        self.assertEqual(server.messages[0].int_msgid, 4002)

    def test_full_agent_session_uses_binary_mswitch_transport(self):
        profile = FrozenProfile.load(PROFILE)
        with LoopbackMswitchTestServer(TEST_UUID) as server:
            transport = LoopbackMswitchTcpTransport(
                "127.0.0.1",
                server.port,
                TEST_UUID,
                test_mode=True,
            )
            session = BidirectionalGuestSession(transport, profile.identity, profile.environment)
            agent = MockTelemetryAgent(profile, transport, control_session=session)
            agent.start()
            agent.run_for(30)
            agent.close()
        ids = [item.int_msgid for item in server.messages]
        self.assertIn(8008, ids)
        self.assertIn(9050, ids)
        self.assertIn(4002, ids)
        self.assertEqual(session.state.name, "HEALTHY")
        version = next(item for item in server.messages if item.int_msgid == 4004)
        self.assertIsNotNone(version.wire_payload)
        self.assertTrue(version.wire_payload.startswith(b"{msgtype:'4004'"))
        self.assertEqual(version.payload["schema"], "vmbooster_4004_v1")


if __name__ == "__main__":
    unittest.main()
