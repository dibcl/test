import unittest

from ice_local_protocol import (
    CURSOR,
    INPUTS,
    LINK_MAGIC,
    PLAYBACK,
    RECORD,
    ZTE_CAPTURE,
    CaptureControlEvent,
    ChannelRegistration,
    ConnectionEvent,
    IceLocalProtocolError,
    LocalMessageType,
    VIDEO_MAP_CONTROL_SIZE,
    VIDEO_MAP_COMMAND_OFFSET,
    VIDEO_MAP_MAX_HEIGHT,
    VIDEO_MAP_MAX_WIDTH,
    VIDEO_MAP_PIXEL_SIZE,
    VIDEO_MAP_TOTAL_SIZE,
    SurfaceCommandType,
    SurfaceCommand,
    DirtyRect,
    VIDEO_MAP_DIRTY_READ_INDEX_OFFSET,
    VIDEO_MAP_DIRTY_RECORD_OFFSET,
    VIDEO_MAP_DIRTY_TIMESTAMP_OFFSET,
    VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET,
    build_link_header,
    create_video_map,
    append_surface_command,
    append_dirty_rect,
    write_primary_frame,
    parse_link_header,
    parse_surface_command_ring,
    parse_dirty_rect_ring,
    validate_video_map_size,
)


class IceLocalProtocolTests(unittest.TestCase):
    def test_link_header_matches_windows_components(self):
        self.assertEqual(build_link_header(), b"\x9a\x00\x00\x00")
        parse_link_header(build_link_header())
        self.assertEqual(LINK_MAGIC, 0x9A)

    def test_rejects_bad_link_header(self):
        with self.assertRaises(IceLocalProtocolError):
            parse_link_header(b"\x9b\x00\x00\x00")
        with self.assertRaises(IceLocalProtocolError):
            parse_link_header(b"\x9a")

    def test_confirmed_registration_bytes(self):
        expected = {
            INPUTS: b"\x43\x04\x06\x00\x03\x00",
            CURSOR: b"\x43\x04\x06\x00\x04\x00",
            PLAYBACK: b"\x43\x04\x06\x00\x05\x00",
            RECORD: b"\x43\x04\x06\x00\x06\x00",
            ZTE_CAPTURE: b"\x43\x04\x06\x00\x0c\x00",
        }
        for registration, encoded in expected.items():
            with self.subTest(channel_type=registration.channel_type):
                self.assertEqual(registration.encode(), encoded)
                self.assertEqual(ChannelRegistration.decode(encoded), registration)

    def test_rejects_bad_registration(self):
        with self.assertRaises(IceLocalProtocolError):
            ChannelRegistration.decode(b"\x00" * 6)
        with self.assertRaises(IceLocalProtocolError):
            ChannelRegistration.decode(b"\x43\x04")
        with self.assertRaises(IceLocalProtocolError):
            ChannelRegistration(256).encode()

    def test_confirmed_capture_control_constants(self):
        self.assertEqual(LocalMessageType.CONTROL, 1)
        self.assertEqual(LocalMessageType.CONNECTION_STATE, 2)
        self.assertEqual(ConnectionEvent.CONNECTED, 0x65)
        self.assertEqual(ConnectionEvent.DISCONNECTED, 0x66)
        self.assertEqual(CaptureControlEvent.CONFIG, 0x44E)
        self.assertEqual(CaptureControlEvent.GPU_PARAMETERS, 0x472)

    def test_confirmed_video_map_geometry(self):
        self.assertEqual(VIDEO_MAP_CONTROL_SIZE, 0x9170)
        self.assertEqual(VIDEO_MAP_MAX_WIDTH, 4096)
        self.assertEqual(VIDEO_MAP_MAX_HEIGHT, 2160)
        self.assertEqual(VIDEO_MAP_PIXEL_SIZE, 35_389_440)
        self.assertEqual(VIDEO_MAP_TOTAL_SIZE, 35_426_672)
        validate_video_map_size(VIDEO_MAP_TOTAL_SIZE)
        with self.assertRaises(IceLocalProtocolError):
            validate_video_map_size(VIDEO_MAP_TOTAL_SIZE - 1)

    def test_surface_command_ring_layout(self):
        image = bytearray(VIDEO_MAP_CONTROL_SIZE)
        image[VIDEO_MAP_COMMAND_OFFSET:VIDEO_MAP_COMMAND_OFFSET + 2] = b"\x02\x00"
        image[VIDEO_MAP_COMMAND_OFFSET + 2:VIDEO_MAP_COMMAND_OFFSET + 18] = (
            b"0824015429570\x00\x00\x00"
        )
        record = VIDEO_MAP_COMMAND_OFFSET + 18 + 15
        image[record:record + 15] = (
            b"\x02\x00\x00\x00\x00"
            b"\x00\x0c\x80\x07\x00\x30\x20\x00\x01\x00"
        )
        ring = parse_surface_command_ring(bytes(image))
        self.assertEqual(ring.next_index, 2)
        self.assertEqual(ring.timestamp, "0824015429570")
        create = ring.commands[1]
        self.assertEqual(create.command_type, SurfaceCommandType.CREATE_PRIMARY)
        self.assertEqual(create.surface_id, 0)
        self.assertEqual((create.width, create.height), (3072, 1920))
        self.assertEqual(create.stride, 12288)
        self.assertEqual(create.bits_per_pixel, 32)
        self.assertEqual(create.flags, 1)

    def test_dirty_rect_ring_layout(self):
        image = bytearray(VIDEO_MAP_CONTROL_SIZE)
        image[
            VIDEO_MAP_DIRTY_TIMESTAMP_OFFSET:VIDEO_MAP_DIRTY_TIMESTAMP_OFFSET + 16
        ] = b"0824035524726\x00\x00\x00"
        image[
            VIDEO_MAP_DIRTY_READ_INDEX_OFFSET:VIDEO_MAP_DIRTY_READ_INDEX_OFFSET + 2
        ] = b"\x01\x00"
        image[
            VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET:VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET + 2
        ] = b"\x02\x00"
        record = VIDEO_MAP_DIRTY_RECORD_OFFSET + 9
        image[record:record + 9] = (
            b"\x00\x0d\x01\x81\x03\x6f\x06\x64\x0a"
        )
        ring = parse_dirty_rect_ring(bytes(image))
        self.assertEqual(ring.timestamp, "0824035524726")
        self.assertEqual((ring.read_index, ring.write_index), (1, 2))
        self.assertEqual(ring.pending_count, 1)
        rect = ring.records[1]
        self.assertEqual((rect.left, rect.top), (897, 269))
        self.assertEqual((rect.right, rect.bottom), (2660, 1647))

    def test_surface_command_writer_round_trip(self):
        image = create_video_map()
        command = SurfaceCommand(
            SurfaceCommandType.CREATE_PRIMARY, 0, 3072, 1920, 12288, 32, 1
        )
        self.assertEqual(append_surface_command(image, command, "0824050000000"), 0)
        ring = parse_surface_command_ring(image)
        self.assertEqual(ring.next_index, 1)
        self.assertEqual(ring.timestamp, "0824050000000")
        self.assertEqual(ring.commands[0], command)

    def test_dirty_rect_writer_round_trip_and_full_guard(self):
        image = create_video_map()
        rect = DirtyRect(6, 10, 20, 110, 220)
        self.assertEqual(append_dirty_rect(image, rect, "0824050000001"), 0)
        ring = parse_dirty_rect_ring(image)
        self.assertEqual((ring.read_index, ring.write_index), (0, 1))
        self.assertEqual(ring.records[0], rect)
        image[VIDEO_MAP_DIRTY_READ_INDEX_OFFSET:VIDEO_MAP_DIRTY_READ_INDEX_OFFSET + 2] = b"\x01\x00"
        image[VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET:VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET + 2] = b"\x00\x00"
        with self.assertRaises(IceLocalProtocolError):
            append_dirty_rect(image, rect, "0824050000002")

    def test_primary_frame_writer(self):
        image = create_video_map()
        frame = bytes(range(16))
        write_primary_frame(image, frame, height=2, stride=8)
        self.assertEqual(
            image[VIDEO_MAP_CONTROL_SIZE:VIDEO_MAP_CONTROL_SIZE + len(frame)], frame
        )
        with self.assertRaises(IceLocalProtocolError):
            write_primary_frame(image, frame, height=2, stride=9)


if __name__ == "__main__":
    unittest.main()
