"""Async TCP client for a local AirJet controller.

The controller is an untrusted peer on the local network, so every read is
bounded and every exchange has a deadline. Nothing here interprets the status
bytes: decoding is the datapoint layout's job, and this module only moves
frames.

The passcode the controller hands out is a device credential. It is held in
memory, never logged and never surfaced in an error.
"""

from __future__ import annotations

import asyncio
from collections import deque
from types import TracebackType
from typing import Self

from homeflow.integrations.base.errors import ProviderUnavailableError
from homeflow.integrations.bestway.protocol import (
    STATUS_READ_PAYLOAD,
    Command,
    Frame,
    FrameReader,
    ProtocolError,
    decode_length_prefixed,
    encode_length_prefixed,
)
from homeflow.log import get_logger

_logger = get_logger(__name__)

_READ_CHUNK = 4096
#: How long a single exchange may take before the controller is declared unreachable.
DEFAULT_REQUEST_TIMEOUT = 5.0
DEFAULT_CONNECT_TIMEOUT = 5.0


class ControllerMisbehaved(ProviderUnavailableError):
    """The peer sent something that is not this protocol.

    Distinct from an ordinary connection failure: a dropped connection is worth
    retrying, a peer that cannot frame correctly is not.
    """


class BestwayClient:
    """One connection to one controller. Not safe to share across tasks."""

    __slots__ = (
        "_connect_timeout",
        "_host",
        "_lock",
        "_passcode",
        "_pending",
        "_port",
        "_reader",
        "_request_timeout",
        "_stream_reader",
        "_writer",
    )

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._stream_reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader = FrameReader()
        self._pending: deque[Frame] = deque()
        self._passcode: bytes = b""
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open the socket and complete the passcode and login exchange."""
        await self.close()
        try:
            async with asyncio.timeout(self._connect_timeout):
                self._stream_reader, self._writer = await asyncio.open_connection(
                    self._host, self._port
                )
        except (TimeoutError, OSError) as exc:
            raise ProviderUnavailableError("the controller did not accept a connection") from exc

        self._reader = FrameReader()
        self._pending.clear()
        try:
            answer = await self._exchange(
                Frame(command=Command.PASSCODE_REQUEST),
                expect=Command.PASSCODE_RESPONSE,
            )
            # The passcode travels as a length-prefixed field, and it goes back
            # the same way.
            self._passcode = decode_length_prefixed(answer)
            await self._exchange(
                Frame(
                    command=Command.LOGIN_REQUEST,
                    payload=encode_length_prefixed(self._passcode),
                ),
                expect=Command.LOGIN_RESPONSE,
            )
        except Exception:
            await self.close()
            raise

        _logger.info("bestway.connected", provider="bestway")

    async def close(self) -> None:
        writer, self._writer, self._stream_reader = self._writer, None, None
        self._passcode = b""
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            # The peer is gone either way; closing must not mask the real error.
            _logger.debug("bestway.close_failed", provider="bestway")

    async def read_status(self) -> bytes:
        """Ask for the current status block and return its raw payload."""
        return await self._exchange(
            Frame(command=Command.STATUS_REQUEST, payload=STATUS_READ_PAYLOAD),
            expect=(Command.STATUS_RESPONSE, Command.STATUS_REPORT),
        )

    async def write_status_payload(self, payload: bytes) -> None:
        """Send one attribute block.

        The payload is produced by a verified datapoint layout; there is no path
        from the HomeFlow API to this method that carries caller-supplied bytes.
        """
        await self._send(Frame(command=Command.WRITE_ATTRIBUTE, payload=payload))

    async def _exchange(
        self,
        request: Frame,
        *,
        expect: Command | tuple[Command, ...],
    ) -> bytes:
        wanted = (expect,) if isinstance(expect, Command) else expect
        async with self._lock:
            await self._send(request)
            try:
                async with asyncio.timeout(self._request_timeout):
                    while True:
                        frame = await self._next_frame()
                        if frame.command in wanted:
                            return frame.payload
                        # Unsolicited traffic is normal on this protocol; the
                        # controller pushes reports and heartbeats of its own.
                        if frame.command == Command.HEARTBEAT_REQUEST:
                            await self._send(Frame(command=Command.HEARTBEAT_RESPONSE))
            except TimeoutError as exc:
                raise ProviderUnavailableError("the controller did not answer in time") from exc

    async def _send(self, frame: Frame) -> None:
        writer = self._writer
        if writer is None:
            raise ProviderUnavailableError("not connected to the controller")
        try:
            writer.write(frame.encode())
            async with asyncio.timeout(self._request_timeout):
                await writer.drain()
        except (OSError, TimeoutError) as exc:
            raise ProviderUnavailableError("the connection to the controller failed") from exc

    async def _next_frame(self) -> Frame:
        while True:
            if self._pending:
                return self._pending.popleft()

            reader = self._stream_reader
            if reader is None:
                raise ProviderUnavailableError("not connected to the controller")
            try:
                chunk = await reader.read(_READ_CHUNK)
            except OSError as exc:
                raise ProviderUnavailableError("the connection to the controller failed") from exc
            if not chunk:
                raise ProviderUnavailableError("the controller closed the connection")

            try:
                self._pending.extend(self._reader.feed(chunk))
            except ProtocolError as exc:
                # A peer that cannot frame correctly is not one to keep talking to.
                _logger.warning("bestway.malformed_frame", provider="bestway")
                raise ControllerMisbehaved("the controller sent a malformed frame") from exc
