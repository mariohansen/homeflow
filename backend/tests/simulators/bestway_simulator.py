"""A stand-in AirJet controller speaking the Gizwits LAN framing.

CI must never depend on the real household, and the adapter must be exercisable
before anyone owns a verified datapoint layout. This simulator answers the same
frames a controller does, so the client, the layout and the provider can all be
tested end to end.

It is also useful by hand::

    python backend/tests/simulators/bestway_simulator.py --port 12416

Every value it serves is synthetic.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import dataclass, field

from homeflow.integrations.bestway.protocol import (
    Command,
    Frame,
    FrameReader,
    decode_length_prefixed,
    encode_length_prefixed,
)

#: Ten bytes, as the protocol specifies. Synthetic.
PASSCODE = b"simcode123"


#: A status block laid out for the candidate profile: flags at offset 4,
#: target temperature at 5, current temperature at 6.
def initial_payload(
    *,
    current_c: int = 24,
    target_c: int = 38,
    heater: bool = False,
    filter_pump: bool = True,
    bubbles: bool = False,
    panel_lock: bool = False,
    fahrenheit: bool = False,
) -> bytes:
    flags = (
        (bubbles << 0) | (filter_pump << 1) | (heater << 2) | (panel_lock << 3) | (fahrenheit << 4)
    )
    return bytes([0x01, 0x00, 0x00, 0x00, flags, target_c, current_c, 0, 0, 0, 0, 0])


def airjet19_payload(
    *,
    current_c: int = 24,
    target_c: int = 38,
    heater: bool = False,
    filter_pump: bool = False,
    bubbles: bool = False,
    panel_lock: bool = False,
) -> bytes:
    """A status block shaped like the airjet-19byte layout.

    Synthetic values only. Byte 0 is the message type, byte 1 the flags, and
    the bytes this layout does not name are left at zero.
    """
    flags = (
        0b0100_0001  # bits 0 and 6 were set throughout the observation
        | (heater << 1)
        | (filter_pump << 2)
        | (bubbles << 3)
        | (panel_lock << 4)
    )
    block = bytearray(19)
    block[0] = 0x03
    block[1] = flags
    block[2] = target_c
    block[15] = current_c
    return bytes(block)


@dataclass(slots=True)
class BestwaySimulator:
    """Serves one synthetic controller.

    ``honour_writes`` off simulates a controller that acknowledges a write at
    the transport level but does not act on it, which is what makes the
    adapter's read-after-write check worth having.
    """

    payload: bytearray = field(default_factory=lambda: bytearray(initial_payload()))
    honour_writes: bool = True
    require_login: bool = True
    #: Send a byte that cannot start a frame, to exercise the client's rejection.
    corrupt_next_response: bool = False
    #: Hang up after answering, as controllers in the field do.
    close_after_response: bool = False

    _server: asyncio.Server | None = field(default=None, init=False)
    _connections: set[asyncio.StreamWriter] = field(default_factory=set, init=False)
    _logged_in: bool = field(default=False, init=False)
    write_count: int = field(default=0, init=False)

    @property
    def port(self) -> int:
        if self._server is None:  # pragma: no cover - callers start first
            raise RuntimeError("the simulator is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = await asyncio.start_server(self._serve, host, port)

    async def stop(self) -> None:
        server, self._server = self._server, None
        # Close live connections first: asyncio.Server.wait_closed waits for
        # every handler, so a client that never hung up would hang teardown.
        for writer in list(self._connections):
            writer.close()
        self._connections.clear()
        if server is None:
            return
        server.close()
        with contextlib.suppress(TimeoutError, Exception):
            async with asyncio.timeout(2.0):
                await server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        frames = FrameReader()
        self._connections.add(writer)
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                for frame in frames.feed(chunk):
                    for response in self._handle(frame):
                        if self.corrupt_next_response:
                            self.corrupt_next_response = False
                            writer.write(b"\xff\xff\xff\xff")
                        else:
                            writer.write(response.encode())
                    await writer.drain()
                    if self.close_after_response and frame.command == Command.STATUS_REQUEST:
                        return
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            return
        finally:
            self._connections.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _handle(self, frame: Frame) -> list[Frame]:
        match frame.command:
            case Command.PASSCODE_REQUEST:
                return [
                    Frame(
                        command=Command.PASSCODE_RESPONSE,
                        payload=encode_length_prefixed(PASSCODE),
                    )
                ]
            case Command.LOGIN_REQUEST:
                presented = decode_length_prefixed(frame.payload)
                self._logged_in = presented == PASSCODE or not self.require_login
                return [Frame(command=Command.LOGIN_RESPONSE, payload=b"\x00")]
            case Command.HEARTBEAT_REQUEST:
                return [Frame(command=Command.HEARTBEAT_RESPONSE)]
            case Command.STATUS_REQUEST:
                if self.require_login and not self._logged_in:
                    return []
                return [Frame(command=Command.STATUS_RESPONSE, payload=bytes(self.payload))]
            case Command.WRITE_ATTRIBUTE:
                self.write_count += 1
                if self.honour_writes:
                    self.payload = bytearray(frame.payload)
                return [Frame(command=Command.STATUS_REPORT, payload=bytes(self.payload))]
            case _:
                return []


async def _main() -> None:  # pragma: no cover - manual tool
    parser = argparse.ArgumentParser(description="Run a synthetic AirJet controller.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12416)
    parser.add_argument(
        "--layout",
        choices=("candidate", "airjet19"),
        default="candidate",
        help="which status block shape to serve",
    )
    args = parser.parse_args()

    block = airjet19_payload() if args.layout == "airjet19" else initial_payload()
    simulator = BestwaySimulator(payload=bytearray(block))
    await simulator.start(args.host, args.port)
    print(f"synthetic AirJet controller listening on {args.host}:{simulator.port}")
    try:
        await asyncio.Event().wait()
    finally:
        await simulator.stop()


if __name__ == "__main__":  # pragma: no cover - manual tool
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
