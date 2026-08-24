import struct
import unittest

from mswitch_protocol import (
    HEADER_SIZE,
    MAGIC,
    MAX_MESSAGE_SIZE,
    REGISTER_MSG_ID,
    ProtocolError,
    SerialFrameDecoder,
    build_message,
    build_register_request,
    encode_serial_frame,
    parse_message,
    parse_register_response,
)


class ProtocolTests(unittest.TestCase):
    def test_build_message_matches_observed_offsets(self):
        uuid = bytes(range(16))
        raw = build_message(
            dst_mod=6,
            uuid=uuid,
            dst_type=1,
            int_msgid=0x8102C0,
            payload=b"{}",
        ).to_bytes()
        self.assertEqual(len(raw), HEADER_SIZE + 2)
        self.assertEqual(struct.unpack_from("<I", raw, 0)[0], MAGIC)
        self.assertEqual(struct.unpack_from("<h", raw, 0x22)[0], 1)
        self.assertEqual(raw[0x24:0x34], uuid)
        self.assertEqual(struct.unpack_from("<I", raw, 0x38)[0], 6)
        self.assertEqual(struct.unpack_from("<I", raw, 0x50)[0], 0x8102C0)
        self.assertEqual(struct.unpack_from("<I", raw, 0x5C)[0], 2)
        self.assertEqual(raw[0x80:], b"{}")
        parsed = parse_message(raw)
        self.assertEqual(parsed.magic, MAGIC)
        self.assertEqual(parsed.version, 1)
        self.assertEqual(parsed.data_len, 2)

    def test_register_request_is_0x84_bytes(self):
        raw = build_register_request(0x80000001).to_bytes()
        self.assertEqual(len(raw), 0x84)
        self.assertEqual(struct.unpack_from("<I", raw, 0x34)[0], 0x80000001)
        self.assertEqual(struct.unpack_from("<I", raw, 0x50)[0], REGISTER_MSG_ID)
        self.assertEqual(raw[0x80:], struct.pack("<I", 0x80000001))

    def test_parse_preserves_unknown_header_bytes(self):
        raw = bytearray(build_message(
            dst_mod=10,
            uuid=b"U" * 16,
            dst_type=1,
            int_msgid=7,
        ).to_bytes())
        raw[0x08:0x0C] = b"KEEP"
        self.assertEqual(parse_message(bytes(raw)).to_bytes(), bytes(raw))

    def test_rejects_bad_magic_and_bad_length(self):
        raw = bytearray(build_register_request(3).to_bytes())
        raw[0] ^= 1
        with self.assertRaises(ProtocolError):
            parse_message(bytes(raw))
        with self.assertRaises(ProtocolError):
            parse_message(build_register_request(3).to_bytes()[:-1])

    def test_rejects_oversize_payload(self):
        with self.assertRaises(ProtocolError):
            build_message(
                dst_mod=1,
                uuid=b"0" * 16,
                dst_type=1,
                int_msgid=1,
                payload=b"x" * (MAX_MESSAGE_SIZE - HEADER_SIZE + 1),
            )

    def test_register_response(self):
        uuid = b"R" * 16
        raw = bytearray(build_message(
            dst_mod=0,
            uuid=b"\0" * 16,
            dst_type=0,
            int_msgid=REGISTER_MSG_ID,
            payload=uuid,
            msgtype=1,
        ).to_bytes())
        self.assertEqual(parse_register_response(bytes(raw)), uuid)

    def test_serial_frame_escapes_backslash_and_semicolon(self):
        original = b"a;\\b"
        framed = encode_serial_frame(original)
        self.assertEqual(framed, b"a\\;\\\\b;")
        decoder = SerialFrameDecoder()
        self.assertEqual(decoder.feed(framed), [original])

    def test_serial_decoder_handles_fragmentation_and_multiple_frames(self):
        first = encode_serial_frame(b"one;two")
        second = encode_serial_frame(b"three\\four")
        decoder = SerialFrameDecoder()
        split = len(first) - 2
        self.assertEqual(decoder.feed(first[:split]), [])
        self.assertEqual(decoder.feed(first[split:] + second), [b"one;two", b"three\\four"])
        decoder.finish()

    def test_serial_decoder_rejects_truncated_final_frame(self):
        decoder = SerialFrameDecoder()
        self.assertEqual(decoder.feed(b"unterminated"), [])
        with self.assertRaises(ProtocolError):
            decoder.finish()

        decoder = SerialFrameDecoder()
        self.assertEqual(decoder.feed(b"dangling\\"), [])
        with self.assertRaises(ProtocolError):
            decoder.finish()


if __name__ == "__main__":
    unittest.main()
