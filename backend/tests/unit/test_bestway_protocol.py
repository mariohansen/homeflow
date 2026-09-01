"""Framing against a peer that is assumed hostile."""

from __future__ import annotations

import pytest

from homeflow.integrations.bestway.protocol import (
    MAGIC,
    MAX_PAYLOAD_BYTES,
    STATUS_READ_PAYLOAD,
    Command,
    Frame,
    FrameReader,
    IncompleteFrame,
    ProtocolError,
    decode_frame,
    decode_length_prefixed,
    decode_varint,
    encode_length_prefixed,
    encode_varint,
)


@pytest.mark.parametrize("value", [0, 1, 127, 128, 300, 16383, 16384])
def test_varint_round_trip(value: int) -> None:
    encoded = encode_varint(value)
    decoded, consumed = decode_varint(encoded)
    assert decoded == value
    assert consumed == len(encoded)


def test_varint_rejects_negative_and_truncated() -> None:
    with pytest.raises(ProtocolError):
        encode_varint(-1)
    with pytest.raises(IncompleteFrame):
        decode_varint(b"\x80")


def test_varint_refuses_an_endless_continuation() -> None:
    with pytest.raises(ProtocolError):
        decode_varint(b"\x80\x80\x80\x80\x80")


def test_frame_round_trip() -> None:
    frame = Frame(command=Command.STATUS_RESPONSE, payload=b"\x01\x02\x03")
    decoded, consumed = decode_frame(frame.encode())
    assert decoded == frame
    assert consumed == len(frame.encode())


def test_frame_without_the_magic_is_refused() -> None:
    with pytest.raises(ProtocolError, match="magic"):
        decode_frame(b"\xff\xff\xff\xff\x05\x00\x00\x90")


def test_truncated_input_asks_for_more_rather_than_failing() -> None:
    encoded = Frame(command=Command.STATUS_RESPONSE, payload=b"abcd").encode()
    for cut in range(1, len(encoded)):
        with pytest.raises(IncompleteFrame):
            decode_frame(encoded[:cut])


def test_an_oversized_declared_length_is_refused_before_allocating() -> None:
    hostile = MAGIC + encode_varint(MAX_PAYLOAD_BYTES + 1) + b"\x00\x00\x90"
    with pytest.raises(ProtocolError, match="maximum"):
        decode_frame(hostile)


def test_a_length_too_small_for_a_command_is_refused() -> None:
    with pytest.raises(ProtocolError, match="too small"):
        decode_frame(MAGIC + encode_varint(2) + b"\x00\x00")


def test_sending_an_oversized_payload_is_refused() -> None:
    with pytest.raises(ProtocolError):
        Frame(command=Command.WRITE_ATTRIBUTE, payload=b"x" * (MAX_PAYLOAD_BYTES + 1)).encode()


def test_an_unknown_command_is_data_not_a_crash() -> None:
    raw = MAGIC + encode_varint(3) + b"\x00\xab\xcd"
    frame, _ = decode_frame(raw)
    assert frame.command == 0xABCD


def test_reader_reassembles_frames_split_across_chunks() -> None:
    first = Frame(command=Command.STATUS_RESPONSE, payload=b"one").encode()
    second = Frame(command=Command.HEARTBEAT_RESPONSE).encode()
    stream = first + second

    reader = FrameReader()
    collected = []
    for index in range(0, len(stream), 3):
        collected.extend(reader.feed(stream[index : index + 3]))

    assert [frame.command for frame in collected] == [
        Command.STATUS_RESPONSE,
        Command.HEARTBEAT_RESPONSE,
    ]
    assert reader.pending_bytes == 0


def test_reader_refuses_to_buffer_endless_garbage() -> None:
    reader = FrameReader()
    with pytest.raises(ProtocolError, match="buffered"):
        reader.feed(MAGIC + b"\x00" * (MAX_PAYLOAD_BYTES * 4 + 1))


def test_reader_rejects_a_stream_that_cannot_be_framed() -> None:
    reader = FrameReader()
    with pytest.raises(ProtocolError, match="magic"):
        reader.feed(b"\xde\xad\xbe\xef\x01\x02")


# Byte sequences taken from the published description of the protocol. They are
# the reference this implementation is measured against, so a change in framing
# has to be a deliberate one.
DOCUMENTED_FRAMES = [
    ("passcode request", Frame(command=Command.PASSCODE_REQUEST), "00000003030000 06"),
    ("heartbeat", Frame(command=Command.HEARTBEAT_REQUEST), "00000003030000 15"),
    (
        "status read",
        Frame(command=Command.STATUS_REQUEST, payload=STATUS_READ_PAYLOAD),
        "00000003040000 9002",
    ),
    (
        "login",
        Frame(command=Command.LOGIN_REQUEST, payload=encode_length_prefixed(b"0123456789")),
        "000000030f0000 08000a30313233343536373839",
    ),
]


@pytest.mark.parametrize(
    ("name", "frame", "expected"),
    DOCUMENTED_FRAMES,
    ids=[entry[0] for entry in DOCUMENTED_FRAMES],
)
def test_frames_match_the_documented_byte_sequences(
    name: str,
    frame: Frame,
    expected: str,
) -> None:
    assert frame.encode().hex() == expected.replace(" ", "")


def test_a_length_prefixed_field_round_trips() -> None:
    assert decode_length_prefixed(encode_length_prefixed(b"0123456789")) == b"0123456789"


def test_a_field_without_the_prefix_is_taken_as_it_is() -> None:
    """Some firmware answers without the prefix; refusing would be worse."""
    assert decode_length_prefixed(b"\xff\xfeabc") == b"\xff\xfeabc"


def test_a_truncated_length_prefixed_field_is_refused() -> None:
    with pytest.raises(ProtocolError, match="truncated"):
        decode_length_prefixed(b"\x01")
