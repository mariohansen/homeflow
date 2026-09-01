"""Framing for the Gizwits GAgent LAN protocol used by AirJet controllers.

The controller speaks a plaintext, unauthenticated protocol on the local
network. Every value arriving on that socket is therefore treated as hostile
input: lengths are bounded before allocation, frames that do not parse are
rejected rather than repaired, and a short read never blocks forever.

Frame layout::

    00 00 00 03        magic
    <varint>           length of everything that follows
    <byte>             flag
    <uint16 be>        command
    <bytes>            payload

The layout and the command numbers below follow the community documentation of
the protocol; see docs/integrations/bestway.md. They have not been confirmed
against a physical controller in this repository, which is exactly what
``scripts/bestway_probe.py`` exists to do. Everything protocol-specific lives in
this module so that a correction touches one file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Final

MAGIC: Final = b"\x00\x00\x00\x03"

#: A controller status frame is tens of bytes. Anything approaching this bound
#: is malformed or hostile, and is refused before memory is reserved for it.
MAX_PAYLOAD_BYTES: Final = 2048
#: Four continuation bytes already encode far more than MAX_PAYLOAD_BYTES.
MAX_VARINT_BYTES: Final = 4


class Command(IntEnum):
    """Commands exchanged with the controller.

    Directions matter here. 0x0093 is app to device and 0x0094 is the device's
    answer to it; sending 0x0094 at a controller is meaningless, which is what
    an earlier version of this adapter did.
    """

    DEVICE_INFO_REQUEST = 0x0003
    DEVICE_INFO_RESPONSE = 0x0004
    PASSCODE_REQUEST = 0x0006
    PASSCODE_RESPONSE = 0x0007
    LOGIN_REQUEST = 0x0008
    LOGIN_RESPONSE = 0x0009
    HEARTBEAT_REQUEST = 0x0015
    HEARTBEAT_RESPONSE = 0x0016
    #: Serial data transmit. Carries a p0 read request and answers on 0x0091.
    STATUS_REQUEST = 0x0090
    STATUS_RESPONSE = 0x0091
    #: Serial data control, app to device: a four byte sequence number followed
    #: by a p0 control payload. The device answers on CONTROL_RESPONSE.
    CONTROL_REQUEST = 0x0093
    CONTROL_RESPONSE = 0x0094


class ProtocolError(Exception):
    """The peer sent something this implementation refuses to interpret."""


#: ``0x0090`` carries a sub-action; ``0x02`` asks the controller for its status.
STATUS_READ_PAYLOAD: Final = b"\x02"

#: Length of the passcode the controller hands out.
PASSCODE_LENGTH: Final = 10


def encode_length_prefixed(data: bytes) -> bytes:
    """Prefix a field with its length, big endian, as the protocol expects."""
    if len(data) > 0xFFFF:
        raise ProtocolError("field is too long to length-prefix")
    return len(data).to_bytes(2, "big") + data


def decode_length_prefixed(payload: bytes) -> bytes:
    """Read one length-prefixed field, tolerating a payload that omits the prefix."""
    if len(payload) < 2:
        raise ProtocolError("length-prefixed field is truncated")
    declared = int.from_bytes(payload[:2], "big")
    body = payload[2:]
    if declared != len(body):
        # Some firmware answers without the prefix. Taking the payload as-is is
        # better than refusing, and it stays bounded either way.
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ProtocolError("length-prefixed field exceeds the permitted maximum")
        return payload
    return body


class P0Action:
    """Action byte that opens a p0 payload, inside the carrier command."""

    CONTROL = 0x01
    READ = 0x02
    READ_RESPONSE = 0x03
    REPORT = 0x04


#: A control payload is the action byte, two bytes of attribute flags and the
#: attribute values block.
CONTROL_ATTR_FLAGS_BYTES: Final = 2
CONTROL_SEQUENCE_BYTES: Final = 4


def build_control_payload(sequence: int, attr_flags: int, attr_vals: bytes) -> bytes:
    """Assemble the body of a control request.

    ``attr_flags`` says which attributes the controller should apply, and goes
    on the wire big-endian: the device byte-swaps it on receipt.
    """
    if not 0 <= sequence < 2**32:
        raise ProtocolError("control sequence number out of range")
    if not 0 <= attr_flags < 2 ** (8 * CONTROL_ATTR_FLAGS_BYTES):
        raise ProtocolError("attribute flags do not fit the field")
    if not attr_vals:
        raise ProtocolError("a control request needs an attribute value block")
    return (
        sequence.to_bytes(CONTROL_SEQUENCE_BYTES, "big")
        + bytes([P0Action.CONTROL])
        + attr_flags.to_bytes(CONTROL_ATTR_FLAGS_BYTES, "big")
        + attr_vals
    )


class IncompleteFrame(ProtocolError):
    """More bytes are needed before a frame can be decoded."""


@dataclass(frozen=True, slots=True)
class Frame:
    command: Command | int
    payload: bytes = b""
    flag: int = 0x00

    def encode(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD_BYTES:
            raise ProtocolError("refusing to send an oversized payload")
        body = bytes([self.flag]) + int(self.command).to_bytes(2, "big") + self.payload
        return MAGIC + encode_varint(len(body)) + body


def encode_varint(value: int) -> bytes:
    """Encode a non-negative integer, seven bits per byte, low group first."""
    if value < 0:
        raise ProtocolError("varint values are never negative")
    out = bytearray()
    while True:
        group = value & 0x7F
        value >>= 7
        out.append(group | (0x80 if value else 0x00))
        if not value:
            break
        if len(out) >= MAX_VARINT_BYTES:
            raise ProtocolError("varint too large to encode")
    return bytes(out)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Return ``(value, bytes_consumed)`` starting at ``offset``."""
    value = 0
    for index in range(MAX_VARINT_BYTES):
        position = offset + index
        if position >= len(data):
            raise IncompleteFrame("varint is truncated")
        byte = data[position]
        value |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return value, index + 1
    raise ProtocolError("varint exceeds the permitted length")


def decode_frame(data: bytes) -> tuple[Frame, int]:
    """Decode one frame from the head of ``data``.

    Returns the frame and how many bytes it consumed. Raises
    :class:`IncompleteFrame` when more data is needed, which is a normal
    condition on a stream, and :class:`ProtocolError` when the bytes cannot be a
    valid frame at all.
    """
    if len(data) < len(MAGIC):
        raise IncompleteFrame("magic is truncated")
    if not data.startswith(MAGIC):
        raise ProtocolError("frame does not start with the protocol magic")

    length, consumed = decode_varint(data, len(MAGIC))
    if length > MAX_PAYLOAD_BYTES:
        raise ProtocolError("declared frame length exceeds the permitted maximum")
    # flag + command
    if length < 3:
        raise ProtocolError("declared frame length is too small to hold a command")

    start = len(MAGIC) + consumed
    end = start + length
    if len(data) < end:
        raise IncompleteFrame("frame body is truncated")

    body = data[start:end]
    flag = body[0]
    raw_command = int.from_bytes(body[1:3], "big")
    command: Command | int
    try:
        command = Command(raw_command)
    except ValueError:
        # An unknown command is data, not a crash: the caller decides.
        command = raw_command
    return Frame(command=command, payload=body[3:], flag=flag), end


class FrameReader:
    """Reassembles frames from a byte stream with a bounded buffer."""

    __slots__ = ("_buffer",)

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Frame]:
        """Add received bytes and return every complete frame they yield."""
        self._buffer.extend(chunk)
        if len(self._buffer) > MAX_PAYLOAD_BYTES * 4:
            self._buffer.clear()
            raise ProtocolError("peer sent more unframed data than can be buffered")

        frames: list[Frame] = []
        while self._buffer:
            try:
                frame, consumed = decode_frame(bytes(self._buffer))
            except IncompleteFrame:
                break
            del self._buffer[:consumed]
            frames.append(frame)
        return frames

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)
