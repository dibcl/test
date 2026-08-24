"""Offline model of the confirmed ICE localhost connection primitives.

This module intentionally contains no socket or process-control code.  It only
models byte sequences that have been confirmed from the signed Windows ICE
components so they can be regression-tested while the remaining IPC schema is
researched.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct


LINK_HEADER = struct.Struct("<I")
LINK_MAGIC = 0x0000009A

REGISTRATION = struct.Struct("<IBB")
REGISTRATION_PREFIX = 0x00060443

VIDEO_MAP_CONTROL_SIZE = 0x9170
VIDEO_MAP_COMMAND_OFFSET = 0x906E
VIDEO_MAP_DIRTY_TIMESTAMP_OFFSET = 0x5A
VIDEO_MAP_DIRTY_READ_INDEX_OFFSET = 0x6A
VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET = 0x6C
VIDEO_MAP_DIRTY_RECORD_OFFSET = 0x6E
VIDEO_MAP_MAX_WIDTH = 4096
VIDEO_MAP_MAX_HEIGHT = 2160
VIDEO_MAP_BYTES_PER_PIXEL = 4
VIDEO_MAP_PIXEL_SIZE = (
    VIDEO_MAP_MAX_WIDTH * VIDEO_MAP_MAX_HEIGHT * VIDEO_MAP_BYTES_PER_PIXEL
)
VIDEO_MAP_TOTAL_SIZE = VIDEO_MAP_CONTROL_SIZE + VIDEO_MAP_PIXEL_SIZE
SURFACE_COMMAND = struct.Struct("<BiHHHHH")
SURFACE_COMMAND_SLOTS = 16
SURFACE_COMMAND_TIMESTAMP_SIZE = 16
DIRTY_RECT = struct.Struct("<BHHHH")
DIRTY_RECT_SLOTS = 4096


class LocalMessageType(IntEnum):
    CONTROL = 1
    CONNECTION_STATE = 2


class ConnectionEvent(IntEnum):
    CONNECTED = 0x65
    DISCONNECTED = 0x66


class CaptureControlEvent(IntEnum):
    CONFIG = 0x44E
    GPU_PARAMETERS = 0x472


class SurfaceCommandType(IntEnum):
    DESTROY_ALL = 0
    DESTROY_PRIMARY = 1
    CREATE_PRIMARY = 2


class IceLocalProtocolError(ValueError):
    """Raised when a confirmed ICE localhost primitive is malformed."""


@dataclass(frozen=True)
class ChannelRegistration:
    channel_type: int
    channel_id: int = 0

    def encode(self) -> bytes:
        if not 0 <= self.channel_type <= 0xFF:
            raise IceLocalProtocolError("channel_type must fit in one byte")
        if not 0 <= self.channel_id <= 0xFF:
            raise IceLocalProtocolError("channel_id must fit in one byte")
        return REGISTRATION.pack(
            REGISTRATION_PREFIX, self.channel_type, self.channel_id
        )

    @classmethod
    def decode(cls, data: bytes) -> "ChannelRegistration":
        if len(data) != REGISTRATION.size:
            raise IceLocalProtocolError(
                f"registration must be {REGISTRATION.size} bytes"
            )
        prefix, channel_type, channel_id = REGISTRATION.unpack(data)
        if prefix != REGISTRATION_PREFIX:
            raise IceLocalProtocolError(
                f"bad registration prefix 0x{prefix:08x}"
            )
        return cls(channel_type=channel_type, channel_id=channel_id)


@dataclass(frozen=True)
class SurfaceCommand:
    command_type: int
    surface_id: int
    width: int
    height: int
    stride: int
    bits_per_pixel: int
    flags: int


@dataclass(frozen=True)
class SurfaceCommandRing:
    next_index: int
    timestamp: str
    commands: tuple[SurfaceCommand, ...]


@dataclass(frozen=True)
class DirtyRect:
    state: int
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class DirtyRectRing:
    read_index: int
    write_index: int
    timestamp: str
    records: tuple[DirtyRect, ...]

    @property
    def pending_count(self) -> int:
        return (self.write_index - self.read_index) % DIRTY_RECT_SLOTS


def _encode_timestamp(timestamp: str) -> bytes:
    try:
        raw = timestamp.encode("ascii")
    except UnicodeEncodeError as exc:
        raise IceLocalProtocolError("timestamp must be ASCII") from exc
    if len(raw) >= SURFACE_COMMAND_TIMESTAMP_SIZE:
        raise IceLocalProtocolError("timestamp must fit in 15 ASCII bytes")
    return raw.ljust(SURFACE_COMMAND_TIMESTAMP_SIZE, b"\0")


def create_video_map() -> bytearray:
    """Allocate an empty map with the confirmed Windows mapping size."""
    return bytearray(VIDEO_MAP_TOTAL_SIZE)


def append_surface_command(
    video_map: bytearray, command: SurfaceCommand, timestamp: str
) -> int:
    """Append a command using the confirmed 16-slot ring layout.

    The returned value is the slot that was written.  Unknown/reserved bytes in
    the map are preserved.
    """
    validate_video_map_size(len(video_map))
    if not 0 <= command.command_type <= 0xFF:
        raise IceLocalProtocolError("surface command type must fit in one byte")
    for name, value in (
        ("width", command.width),
        ("height", command.height),
        ("stride", command.stride),
        ("bits_per_pixel", command.bits_per_pixel),
        ("flags", command.flags),
    ):
        if not 0 <= value <= 0xFFFF:
            raise IceLocalProtocolError(f"{name} must fit in u16")
    slot = struct.unpack_from("<H", video_map, VIDEO_MAP_COMMAND_OFFSET)[0]
    if slot >= SURFACE_COMMAND_SLOTS:
        raise IceLocalProtocolError(f"bad surface command index {slot}")
    ts_start = VIDEO_MAP_COMMAND_OFFSET + 2
    video_map[ts_start:ts_start + SURFACE_COMMAND_TIMESTAMP_SIZE] = (
        _encode_timestamp(timestamp)
    )
    record_start = ts_start + SURFACE_COMMAND_TIMESTAMP_SIZE
    SURFACE_COMMAND.pack_into(
        video_map,
        record_start + slot * SURFACE_COMMAND.size,
        command.command_type,
        command.surface_id,
        command.width,
        command.height,
        command.stride,
        command.bits_per_pixel,
        command.flags,
    )
    struct.pack_into(
        "<H", video_map, VIDEO_MAP_COMMAND_OFFSET, (slot + 1) % SURFACE_COMMAND_SLOTS
    )
    return slot


def append_dirty_rect(
    video_map: bytearray, rect: DirtyRect, timestamp: str
) -> int:
    """Append a dirty rectangle without overwriting an unread ring record."""
    validate_video_map_size(len(video_map))
    read_index = struct.unpack_from(
        "<H", video_map, VIDEO_MAP_DIRTY_READ_INDEX_OFFSET
    )[0]
    write_index = struct.unpack_from(
        "<H", video_map, VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET
    )[0]
    if read_index >= DIRTY_RECT_SLOTS or write_index >= DIRTY_RECT_SLOTS:
        raise IceLocalProtocolError(
            f"bad dirty ring indices read={read_index} write={write_index}"
        )
    next_index = (write_index + 1) % DIRTY_RECT_SLOTS
    if next_index == read_index:
        raise IceLocalProtocolError("dirty ring is full")
    if not 0 <= rect.state <= 0xFF:
        raise IceLocalProtocolError("dirty state must fit in one byte")
    for name, value in (
        ("left", rect.left),
        ("top", rect.top),
        ("right", rect.right),
        ("bottom", rect.bottom),
    ):
        if not 0 <= value <= 0xFFFF:
            raise IceLocalProtocolError(f"{name} must fit in u16")
    if rect.left > rect.right or rect.top > rect.bottom:
        raise IceLocalProtocolError("dirty rectangle has inverted coordinates")
    video_map[
        VIDEO_MAP_DIRTY_TIMESTAMP_OFFSET:
        VIDEO_MAP_DIRTY_TIMESTAMP_OFFSET + SURFACE_COMMAND_TIMESTAMP_SIZE
    ] = _encode_timestamp(timestamp)
    DIRTY_RECT.pack_into(
        video_map,
        VIDEO_MAP_DIRTY_RECORD_OFFSET + write_index * DIRTY_RECT.size,
        rect.state,
        rect.top,
        rect.left,
        rect.bottom,
        rect.right,
    )
    struct.pack_into(
        "<H", video_map, VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET, next_index
    )
    return write_index


def write_primary_frame(
    video_map: bytearray, frame: bytes, *, height: int, stride: int
) -> None:
    """Write packed frame bytes at the confirmed pixel-area offset."""
    validate_video_map_size(len(video_map))
    if not 0 < height <= VIDEO_MAP_MAX_HEIGHT:
        raise IceLocalProtocolError("height is outside the confirmed map geometry")
    if not 0 < stride <= VIDEO_MAP_MAX_WIDTH * VIDEO_MAP_BYTES_PER_PIXEL:
        raise IceLocalProtocolError("stride is outside the confirmed map geometry")
    expected = height * stride
    if len(frame) != expected:
        raise IceLocalProtocolError(
            f"frame must contain height*stride={expected} bytes, got {len(frame)}"
        )
    video_map[VIDEO_MAP_CONTROL_SIZE:VIDEO_MAP_CONTROL_SIZE + expected] = frame


def parse_surface_command_ring(video_map: bytes) -> SurfaceCommandRing:
    if len(video_map) < VIDEO_MAP_CONTROL_SIZE:
        raise IceLocalProtocolError("video map is shorter than its control area")
    start = VIDEO_MAP_COMMAND_OFFSET
    next_index = struct.unpack_from("<H", video_map, start)[0]
    if next_index >= SURFACE_COMMAND_SLOTS:
        raise IceLocalProtocolError(f"bad surface command index {next_index}")
    ts_start = start + 2
    ts_raw = video_map[ts_start:ts_start + SURFACE_COMMAND_TIMESTAMP_SIZE]
    timestamp = ts_raw.split(b"\0", 1)[0].decode("ascii", "strict")
    record_start = ts_start + SURFACE_COMMAND_TIMESTAMP_SIZE
    commands = []
    for slot in range(SURFACE_COMMAND_SLOTS):
        values = SURFACE_COMMAND.unpack_from(
            video_map, record_start + slot * SURFACE_COMMAND.size
        )
        commands.append(SurfaceCommand(*values))
    return SurfaceCommandRing(next_index, timestamp, tuple(commands))


def parse_dirty_rect_ring(video_map: bytes) -> DirtyRectRing:
    if len(video_map) < VIDEO_MAP_CONTROL_SIZE:
        raise IceLocalProtocolError("video map is shorter than its control area")
    timestamp_raw = video_map[
        VIDEO_MAP_DIRTY_TIMESTAMP_OFFSET:
        VIDEO_MAP_DIRTY_TIMESTAMP_OFFSET + SURFACE_COMMAND_TIMESTAMP_SIZE
    ]
    timestamp = timestamp_raw.split(b"\0", 1)[0].decode("ascii", "strict")
    read_index = struct.unpack_from(
        "<H", video_map, VIDEO_MAP_DIRTY_READ_INDEX_OFFSET
    )[0]
    write_index = struct.unpack_from(
        "<H", video_map, VIDEO_MAP_DIRTY_WRITE_INDEX_OFFSET
    )[0]
    if read_index >= DIRTY_RECT_SLOTS or write_index >= DIRTY_RECT_SLOTS:
        raise IceLocalProtocolError(
            f"bad dirty ring indices read={read_index} write={write_index}"
        )
    records = []
    for slot in range(DIRTY_RECT_SLOTS):
        state, top, left, bottom, right = DIRTY_RECT.unpack_from(
            video_map, VIDEO_MAP_DIRTY_RECORD_OFFSET + slot * DIRTY_RECT.size
        )
        records.append(DirtyRect(state, left, top, right, bottom))
    return DirtyRectRing(read_index, write_index, timestamp, tuple(records))


def build_link_header() -> bytes:
    return LINK_HEADER.pack(LINK_MAGIC)


def parse_link_header(data: bytes) -> None:
    if len(data) != LINK_HEADER.size:
        raise IceLocalProtocolError(f"link header must be {LINK_HEADER.size} bytes")
    (magic,) = LINK_HEADER.unpack(data)
    if magic != LINK_MAGIC:
        raise IceLocalProtocolError(f"bad link magic 0x{magic:08x}")


def validate_video_map_size(size: int) -> None:
    if size != VIDEO_MAP_TOTAL_SIZE:
        raise IceLocalProtocolError(
            f"video map must be {VIDEO_MAP_TOTAL_SIZE} bytes, got {size}"
        )


# Confirmed registrations.  Types 3/4/5/6 match SPICE channel numbering;
# capture type 12 is a ZTE extension in this build.
INPUTS = ChannelRegistration(3)
CURSOR = ChannelRegistration(4)
PLAYBACK = ChannelRegistration(5)
RECORD = ChannelRegistration(6)
ZTE_CAPTURE = ChannelRegistration(12)
