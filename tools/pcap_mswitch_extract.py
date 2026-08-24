"""Extract Mswitch frames from an offline PCAP/PCAPNG and compare fixtures.

This tool performs no capture and opens no network endpoint. It supports the
common Ethernet, Linux cooked-capture and raw-IP link types, reconstructs TCP
payload runs per direction, and recognizes raw or serial-byte-stuffed Mswitch
frames.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import socket
import struct
import sys
from typing import Iterable, Iterator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOTYPE = os.path.join(ROOT, "prototype")
if PROTOTYPE not in sys.path:
    sys.path.insert(0, PROTOTYPE)
if os.path.dirname(__file__) not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))

from mswitch_golden_diff import compare
from mswitch_protocol import HEADER_SIZE, MAGIC, MAX_MESSAGE_SIZE, ProtocolError, SerialFrameDecoder, parse_message


MAGIC_BYTES = struct.pack("<I", MAGIC)
LINKTYPE_NULL = 0
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_LINUX_SLL = 113
LINKTYPE_IPV4 = 228
LINKTYPE_IPV6 = 229
LINKTYPE_LINUX_SLL2 = 276


class CaptureError(ValueError):
    pass


@dataclass(frozen=True)
class Packet:
    index: int
    linktype: int
    data: bytes


@dataclass(frozen=True)
class TcpSegment:
    packet_index: int
    source: str
    source_port: int
    destination: str
    destination_port: int
    sequence: int
    payload: bytes

    @property
    def direction(self) -> str:
        return f"{self.source}_{self.source_port}--{self.destination}_{self.destination_port}"


@dataclass(frozen=True)
class StreamRun:
    direction: str
    run_index: int
    first_packet: int
    last_packet: int
    data: bytes


@dataclass(frozen=True)
class ExtractedFrame:
    direction: str
    run_index: int
    frame_index: int
    stream_offset: int
    encoding: str
    first_packet: int
    last_packet: int
    int_msgid: int
    src_mod: int
    dst_mod: int
    msgtype: int
    dst_type: int
    uuid_hex: str
    data_len: int
    raw: bytes

    def manifest(self, filename: str) -> dict[str, object]:
        value = asdict(self)
        value.pop("raw")
        value["int_msgid_hex"] = f"0x{self.int_msgid:08x}"
        value["filename"] = filename
        return value


def read_capture(path: str | os.PathLike[str]) -> list[Packet]:
    raw = Path(path).read_bytes()
    if len(raw) < 4:
        raise CaptureError("capture is shorter than four bytes")
    if raw[:4] == b"\x0a\x0d\x0d\x0a":
        return list(_read_pcapng(raw))
    return list(_read_pcap(raw))


def _read_pcap(raw: bytes) -> Iterator[Packet]:
    magics = {
        b"\xd4\xc3\xb2\xa1": "<",
        b"\xa1\xb2\xc3\xd4": ">",
        b"\x4d\x3c\xb2\xa1": "<",
        b"\xa1\xb2\x3c\x4d": ">",
    }
    endian = magics.get(raw[:4])
    if endian is None:
        raise CaptureError("unsupported PCAP/PCAPNG magic")
    if len(raw) < 24:
        raise CaptureError("truncated PCAP global header")
    linktype = struct.unpack_from(endian + "I", raw, 20)[0]
    offset = 24
    index = 0
    while offset < len(raw):
        if offset + 16 > len(raw):
            raise CaptureError("truncated PCAP packet header")
        captured = struct.unpack_from(endian + "I", raw, offset + 8)[0]
        offset += 16
        if offset + captured > len(raw):
            raise CaptureError("truncated PCAP packet data")
        index += 1
        yield Packet(index, linktype, raw[offset:offset + captured])
        offset += captured


def _read_pcapng(raw: bytes) -> Iterator[Packet]:
    offset = 0
    endian: str | None = None
    interfaces: list[int] = []
    index = 0
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise CaptureError("truncated PCAPNG block")
        block_type_bytes = raw[offset:offset + 4]
        if block_type_bytes == b"\x0a\x0d\x0d\x0a":
            if offset + 12 > len(raw):
                raise CaptureError("truncated PCAPNG section header")
            bom = raw[offset + 8:offset + 12]
            if bom == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif bom == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                raise CaptureError("invalid PCAPNG byte-order magic")
            interfaces = []
        if endian is None:
            raise CaptureError("PCAPNG does not start with a section header")
        block_type, block_length = struct.unpack_from(endian + "II", raw, offset)
        if block_length < 12 or block_length % 4 or offset + block_length > len(raw):
            raise CaptureError("invalid PCAPNG block length")
        if struct.unpack_from(endian + "I", raw, offset + block_length - 4)[0] != block_length:
            raise CaptureError("PCAPNG block length trailer mismatch")
        if block_type == 1:
            if block_length < 20:
                raise CaptureError("truncated PCAPNG interface block")
            interfaces.append(struct.unpack_from(endian + "H", raw, offset + 8)[0])
        elif block_type == 6:
            if block_length < 32:
                raise CaptureError("truncated PCAPNG enhanced packet block")
            interface_id, captured = struct.unpack_from(endian + "II", raw, offset + 8)[0], struct.unpack_from(endian + "I", raw, offset + 20)[0]
            if interface_id >= len(interfaces):
                raise CaptureError("PCAPNG packet references an unknown interface")
            data_start = offset + 28
            if data_start + captured > offset + block_length - 4:
                raise CaptureError("truncated PCAPNG enhanced packet data")
            index += 1
            yield Packet(index, interfaces[interface_id], raw[data_start:data_start + captured])
        elif block_type == 3:
            if not interfaces or block_length < 16:
                raise CaptureError("invalid PCAPNG simple packet block")
            original_length = struct.unpack_from(endian + "I", raw, offset + 8)[0]
            available = block_length - 16
            captured = min(original_length, available)
            index += 1
            yield Packet(index, interfaces[0], raw[offset + 12:offset + 12 + captured])
        offset += block_length


def tcp_segments(packets: Iterable[Packet]) -> list[TcpSegment]:
    result: list[TcpSegment] = []
    for packet in packets:
        network = _network_payload(packet.linktype, packet.data)
        if network is None:
            continue
        version, payload = network
        parsed = _ipv4_tcp(payload) if version == 4 else _ipv6_tcp(payload)
        if parsed is None:
            continue
        source, sport, destination, dport, sequence, tcp_payload = parsed
        if tcp_payload:
            result.append(TcpSegment(packet.index, source, sport, destination, dport, sequence, tcp_payload))
    return result


def _network_payload(linktype: int, data: bytes) -> tuple[int, bytes] | None:
    if linktype == LINKTYPE_ETHERNET:
        if len(data) < 14:
            return None
        offset = 14
        ethertype = struct.unpack_from("!H", data, 12)[0]
        while ethertype in {0x8100, 0x88A8, 0x9100}:
            if len(data) < offset + 4:
                return None
            ethertype = struct.unpack_from("!H", data, offset + 2)[0]
            offset += 4
        if ethertype == 0x0800:
            return 4, data[offset:]
        if ethertype == 0x86DD:
            return 6, data[offset:]
        return None
    if linktype == LINKTYPE_LINUX_SLL:
        if len(data) < 16:
            return None
        protocol = struct.unpack_from("!H", data, 14)[0]
        return (4, data[16:]) if protocol == 0x0800 else ((6, data[16:]) if protocol == 0x86DD else None)
    if linktype == LINKTYPE_LINUX_SLL2:
        if len(data) < 20:
            return None
        protocol = struct.unpack_from("!H", data, 0)[0]
        return (4, data[20:]) if protocol == 0x0800 else ((6, data[20:]) if protocol == 0x86DD else None)
    if linktype in {LINKTYPE_RAW, LINKTYPE_IPV4, LINKTYPE_IPV6}:
        if not data:
            return None
        version = 4 if linktype == LINKTYPE_IPV4 else (6 if linktype == LINKTYPE_IPV6 else data[0] >> 4)
        return (version, data) if version in {4, 6} else None
    if linktype == LINKTYPE_NULL and len(data) >= 5:
        version = data[4] >> 4
        return (version, data[4:]) if version in {4, 6} else None
    return None


def _ipv4_tcp(data: bytes) -> tuple[str, int, str, int, int, bytes] | None:
    if len(data) < 20 or data[0] >> 4 != 4:
        return None
    ihl = (data[0] & 0x0F) * 4
    total = struct.unpack_from("!H", data, 2)[0]
    flags_offset = struct.unpack_from("!H", data, 6)[0]
    if ihl < 20 or len(data) < ihl or data[9] != 6 or (flags_offset & 0x3FFF):
        return None
    end = min(len(data), total) if total else len(data)
    return _tcp(data[ihl:end], socket.inet_ntop(socket.AF_INET, data[12:16]), socket.inet_ntop(socket.AF_INET, data[16:20]))


def _ipv6_tcp(data: bytes) -> tuple[str, int, str, int, int, bytes] | None:
    if len(data) < 40 or data[0] >> 4 != 6:
        return None
    next_header = data[6]
    offset = 40
    end = min(len(data), 40 + struct.unpack_from("!H", data, 4)[0])
    while next_header in {0, 43, 60}:
        if offset + 2 > end:
            return None
        next_header, units = data[offset], data[offset + 1]
        offset += (units + 1) * 8
    if next_header == 44:
        if offset + 8 > end or struct.unpack_from("!H", data, offset + 2)[0] & 0xFFF8:
            return None
        next_header = data[offset]
        offset += 8
    if next_header != 6 or offset > end:
        return None
    return _tcp(data[offset:end], socket.inet_ntop(socket.AF_INET6, data[8:24]), socket.inet_ntop(socket.AF_INET6, data[24:40]))


def _tcp(data: bytes, source: str, destination: str) -> tuple[str, int, str, int, int, bytes] | None:
    if len(data) < 20:
        return None
    sport, dport, sequence = struct.unpack_from("!HHI", data, 0)
    header_length = (data[12] >> 4) * 4
    if header_length < 20 or header_length > len(data):
        return None
    if data[13] & 0x02:
        sequence = (sequence + 1) & 0xFFFFFFFF
    return source, sport, destination, dport, sequence, data[header_length:]


def reassemble_runs(segments: Iterable[TcpSegment]) -> list[StreamRun]:
    groups: dict[str, list[TcpSegment]] = {}
    for segment in segments:
        groups.setdefault(segment.direction, []).append(segment)
    runs: list[StreamRun] = []
    for direction, values in sorted(groups.items()):
        ordered = sorted(values, key=lambda item: (item.sequence, item.packet_index))
        buffer = bytearray()
        expected: int | None = None
        first_packet = last_packet = 0
        run_index = 0
        for segment in ordered:
            if expected is None or segment.sequence > expected:
                if buffer:
                    runs.append(StreamRun(direction, run_index, first_packet, last_packet, bytes(buffer)))
                    run_index += 1
                buffer = bytearray(segment.payload)
                expected = segment.sequence + len(segment.payload)
                first_packet = last_packet = segment.packet_index
                continue
            overlap = expected - segment.sequence
            if overlap < len(segment.payload):
                buffer.extend(segment.payload[overlap:])
                expected += len(segment.payload) - overlap
                last_packet = max(last_packet, segment.packet_index)
        if buffer:
            runs.append(StreamRun(direction, run_index, first_packet, last_packet, bytes(buffer)))
    return runs


def extract_frames(runs: Iterable[StreamRun], mode: str = "auto", only_msgid: int | None = None) -> list[ExtractedFrame]:
    if mode not in {"auto", "raw", "serial"}:
        raise CaptureError(f"unsupported extraction mode: {mode}")
    output: list[ExtractedFrame] = []
    seen: set[tuple[str, int, int, bytes]] = set()
    for run in runs:
        candidates: list[tuple[int, str, bytes]] = []
        if mode in {"auto", "raw"}:
            candidates.extend(_scan_raw(run.data))
        if mode in {"auto", "serial"}:
            candidates.extend(_scan_serial(run.data))
        frame_index = 0
        for offset, encoding, raw in sorted(candidates, key=lambda item: (item[0], item[1])):
            key = (run.direction, run.run_index, offset, raw)
            if key in seen:
                continue
            seen.add(key)
            try:
                message = parse_message(raw)
            except ProtocolError:
                continue
            if only_msgid is not None and message.int_msgid != only_msgid:
                continue
            output.append(ExtractedFrame(
                run.direction,
                run.run_index,
                frame_index,
                offset,
                encoding,
                run.first_packet,
                run.last_packet,
                message.int_msgid,
                message.src_mod,
                message.dst_mod,
                message.msgtype,
                message.dst_type,
                message.uuid.hex(),
                message.data_len,
                raw,
            ))
            frame_index += 1
    return output


def _scan_raw(data: bytes) -> list[tuple[int, str, bytes]]:
    result: list[tuple[int, str, bytes]] = []
    offset = 0
    while True:
        offset = data.find(MAGIC_BYTES, offset)
        if offset < 0:
            return result
        if offset + HEADER_SIZE <= len(data):
            version = struct.unpack_from("<I", data, offset + 4)[0]
            payload_length = struct.unpack_from("<I", data, offset + 0x5C)[0]
            size = HEADER_SIZE + payload_length
            if version == 1 and size <= MAX_MESSAGE_SIZE and offset + size <= len(data):
                raw = data[offset:offset + size]
                try:
                    parse_message(raw)
                    result.append((offset, "raw", raw))
                    offset += size
                    continue
                except ProtocolError:
                    pass
        offset += 1


def _scan_serial(data: bytes) -> list[tuple[int, str, bytes]]:
    decoder = SerialFrameDecoder()
    result: list[tuple[int, str, bytes]] = []
    frame_start = 0
    for index, value in enumerate(data):
        try:
            frames = decoder.feed(bytes((value,)))
        except ProtocolError:
            decoder.reset()
            frame_start = index + 1
            continue
        for raw in frames:
            try:
                parse_message(raw)
                result.append((frame_start, "serial", raw))
            except ProtocolError:
                pass
            frame_start = index + 1
    return result


def write_results(frames: list[ExtractedFrame], output_dir: str | os.PathLike[str], golden: str | None = None) -> dict[str, object]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    for index, frame in enumerate(frames):
        safe_direction = frame.direction.replace(":", "_").replace("%", "_")
        filename = f"{index:04d}_{safe_direction}_run{frame.run_index}_{frame.int_msgid:08x}.bin"
        (destination / filename).write_bytes(frame.raw)
        manifest.append(frame.manifest(filename))
    report: dict[str, object] = {"frame_count": len(frames), "frames": manifest}
    if golden is not None:
        expected = Path(golden).read_bytes()
        try:
            expected_msgid = parse_message(expected).int_msgid
        except ProtocolError as exc:
            raise CaptureError(f"golden fixture is not a raw Mswitch frame: {exc}") from exc
        matches = [(item, compare(expected, item.raw, False)) for item in frames if item.int_msgid == expected_msgid]
        report["golden"] = str(Path(golden).resolve())
        report["comparisons"] = [
            {"frame_index": frames.index(frame), **comparison}
            for frame, comparison in matches
        ]
    (destination / "manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="offline .pcap or .pcapng file")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mode", choices=("auto", "raw", "serial"), default="auto")
    parser.add_argument("--msgid", type=parse_int, help="optional decimal or 0x-prefixed message ID")
    parser.add_argument("--golden", help="optional raw .bin fixture compared against matching message IDs")
    args = parser.parse_args()
    packets = read_capture(args.capture)
    segments = tcp_segments(packets)
    runs = reassemble_runs(segments)
    frames = extract_frames(runs, args.mode, args.msgid)
    report = write_results(frames, args.output_dir, args.golden)
    print(json.dumps({
        "packets": len(packets),
        "tcp_payload_segments": len(segments),
        "stream_runs": len(runs),
        "frames": report["frame_count"],
        "manifest": str((Path(args.output_dir) / "manifest.json").resolve()),
    }, indent=2))
    return 0 if frames else 2


if __name__ == "__main__":
    raise SystemExit(main())
