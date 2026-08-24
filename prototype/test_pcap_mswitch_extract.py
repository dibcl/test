import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from pcap_mswitch_extract import extract_frames, read_capture, reassemble_runs, tcp_segments, write_results
from mswitch_protocol import build_message, encode_serial_frame


def ethernet_ipv4_tcp(payload, sequence, sport=10000, dport=20000):
    tcp = bytearray(20)
    struct.pack_into("!HHII", tcp, 0, sport, dport, sequence, 0)
    tcp[12] = 5 << 4
    tcp[13] = 0x18
    ip = bytearray(20)
    ip[0] = 0x45
    struct.pack_into("!H", ip, 2, 20 + len(tcp) + len(payload))
    ip[8] = 64
    ip[9] = 6
    ip[12:16] = socket.inet_aton("192.0.2.10")
    ip[16:20] = socket.inet_aton("192.0.2.20")
    ethernet = b"\x00" * 12 + b"\x08\x00"
    return ethernet + bytes(ip) + bytes(tcp) + payload


def pcap_bytes(packets):
    result = bytearray(b"\xd4\xc3\xb2\xa1")
    result.extend(struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1))
    for index, packet in enumerate(packets, 1):
        result.extend(struct.pack("<IIII", index, 0, len(packet), len(packet)))
        result.extend(packet)
    return bytes(result)


def pcapng_bytes(packet):
    def block(block_type, body):
        padding = b"\x00" * ((-len(body)) % 4)
        size = 12 + len(body) + len(padding)
        return struct.pack("<II", block_type, size) + body + padding + struct.pack("<I", size)

    section = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    interface = block(1, struct.pack("<HHI", 1, 0, 65535))
    enhanced = block(6, struct.pack("<IIIII", 0, 0, 1, len(packet), len(packet)) + packet)
    return section + interface + enhanced


class PcapMswitchExtractTests(unittest.TestCase):
    def setUp(self):
        self.message = build_message(
            dst_mod=6,
            uuid=b"P" * 16,
            dst_type=1,
            int_msgid=8047,
            payload=b'{"fixture":true}',
            src_mod=0x80000001,
        ).to_bytes()

    def test_reassembles_out_of_order_tcp_and_extracts_raw_frame(self):
        split = 47
        packets = [
            ethernet_ipv4_tcp(self.message[split:], 1000 + split),
            ethernet_ipv4_tcp(self.message[:split], 1000),
            ethernet_ipv4_tcp(self.message[:split], 1000),
        ]
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "fixture.pcap"
            capture.write_bytes(pcap_bytes(packets))
            parsed = read_capture(capture)
            runs = reassemble_runs(tcp_segments(parsed))
            frames = extract_frames(runs, mode="raw", only_msgid=8047)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].raw, self.message)
        self.assertEqual(frames[0].int_msgid, 8047)

    def test_extracts_serial_framed_message_and_writes_diff_manifest(self):
        wire = encode_serial_frame(self.message)
        packets = [ethernet_ipv4_tcp(wire[index:index + 1], 5000 + index) for index in range(len(wire))]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "fixture.pcap"
            golden = root / "golden.bin"
            output = root / "out"
            capture.write_bytes(pcap_bytes(packets))
            golden.write_bytes(self.message)
            frames = extract_frames(reassemble_runs(tcp_segments(read_capture(capture))), mode="serial")
            report = write_results(frames, output, str(golden))
            manifest = (output / "manifest.json").read_text(encoding="utf-8")
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].encoding, "serial")
        self.assertTrue(report["comparisons"][0]["equal"])
        self.assertIn('"int_msgid": 8047', manifest)

    def test_reads_pcapng_enhanced_packet(self):
        packet = ethernet_ipv4_tcp(self.message, 7000)
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "fixture.pcapng"
            capture.write_bytes(pcapng_bytes(packet))
            frames = extract_frames(reassemble_runs(tcp_segments(read_capture(capture))), mode="auto")
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].int_msgid, 8047)

    def test_auto_mode_preserves_repeated_identical_frames(self):
        stream = self.message + self.message
        packets = [ethernet_ipv4_tcp(stream, 9000)]
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "repeat.pcap"
            capture.write_bytes(pcap_bytes(packets))
            frames = extract_frames(reassemble_runs(tcp_segments(read_capture(capture))), mode="auto")
        self.assertEqual(len(frames), 2)


if __name__ == "__main__":
    unittest.main()
