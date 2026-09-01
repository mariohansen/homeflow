"""Authenticated live state stream (see docs/architecture/overview.md and SECURITY.md).

The socket carries normalised events only. Clients take an initial REST snapshot
and then follow this stream; if the server ever has to drop events for a slow
consumer it says so explicitly with a resync frame rather than letting the client
believe it is in sync.

Two tasks run per connection, but only one of them ever writes to the socket and
only ``stream`` ever closes it. Concurrent sends on one WebSocket corrupt the
connection state, and a send after the peer has gone raises ``RuntimeError``
rather than ``WebSocketDisconnect`` — both are handled in one place here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from homeflow.api.deps import authenticate_websocket, get_websocket_container
from homeflow.api.schemas import DeviceResponse
from homeflow.container import Container
from homeflow.errors import HomeFlowError
from homeflow.events.bus import Subscription
from homeflow.events.models import DomainEvent
from homeflow.log import get_logger
from homeflow.ratelimit import RateLimiter

_logger = get_logger(__name__)

router = APIRouter(prefix="/v1")

PROTOCOL_VERSION = 1
HEARTBEAT_SECONDS = 20.0
_MAX_INBOUND_BYTES = 4096
_ACCEPTED_INBOUND_TYPES = frozenset({"Ping", "Pong"})

_CLOSE_POLICY_VIOLATION = 1008

#: A peer that has gone away surfaces either as a disconnect or, once the close
#: frame has been exchanged, as a RuntimeError from the transport.
_PEER_GONE = (WebSocketDisconnect, RuntimeError)


@dataclass(slots=True)
class _Session:
    """Shared per-connection state between the two tasks."""

    close_code: int | None = None


@router.websocket("/ws")
async def stream(websocket: WebSocket) -> None:
    container = get_websocket_container(websocket)
    try:
        principal, subprotocol = authenticate_websocket(websocket, container)
    except HomeFlowError:
        with contextlib.suppress(*_PEER_GONE):
            await websocket.close(code=_CLOSE_POLICY_VIOLATION)
        return

    # A browser aborts the connection unless the server echoes one of the
    # subprotocols it offered.
    await websocket.accept(subprotocol=subprotocol)
    hello_delivered = await _send(
        websocket,
        {
            "type": "Hello",
            "protocolVersion": PROTOCOL_VERSION,
            "serverTime": container.clock.now().isoformat(),
            "demoMode": container.settings.demo_mode,
        },
    )
    if not hello_delivered:
        return

    _logger.info("ws.connected", client_id=str(principal.client_id))

    inbound_budget = RateLimiter(
        rate_per_minute=container.settings.websocket_rate_limit_per_minute,
        clock=container.clock,
    )
    session = _Session()

    with container.bus.subscribe() as subscription:
        sender = asyncio.create_task(_forward(websocket, subscription, container))
        receiver = asyncio.create_task(_receive(websocket, inbound_budget, session))
        _, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        # Retrieve every outcome so no task exception is left unobserved.
        await asyncio.gather(sender, receiver, return_exceptions=True)

    if session.close_code is not None:
        with contextlib.suppress(*_PEER_GONE):
            await websocket.close(code=session.close_code)

    _logger.info("ws.disconnected", client_id=str(principal.client_id))


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    """Send one frame. Returns False once the peer is gone."""
    try:
        await websocket.send_json(payload)
    except _PEER_GONE:
        return False
    return True


async def _forward(
    websocket: WebSocket,
    subscription: Subscription,
    container: Container,
) -> None:
    iterator = subscription.__aiter__()
    while True:
        try:
            async with asyncio.timeout(HEARTBEAT_SECONDS):
                event = await iterator.__anext__()
        except TimeoutError:
            heartbeat = {"type": "Ping", "serverTime": container.clock.now().isoformat()}
            if not await _send(websocket, heartbeat):
                return
            continue
        except StopAsyncIteration:
            return

        # Events were dropped for this connection: the client must refetch the
        # snapshot instead of trusting its incremental view.
        lagged = subscription.take_lagged()
        if lagged and not await _send(websocket, {"type": "ResyncRequired"}):
            return
        if not await _send(websocket, _frame(event, container)):
            return


def _frame(event: DomainEvent, container: Container) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": event.type.value,
        "occurredAt": event.occurred_at.isoformat(),
    }
    if event.correlation_id is not None:
        frame["correlationId"] = event.correlation_id
    if event.command_id is not None:
        frame["commandId"] = str(event.command_id)
    if event.payload:
        frame["payload"] = event.payload
    if event.device_id is not None:
        frame["deviceId"] = str(event.device_id)
        device = container.registry.get(event.device_id)
        if device is not None:
            frame["device"] = DeviceResponse.from_domain(
                device,
                now=container.clock.now(),
                stale_after_seconds=container.settings.stale_after_seconds,
            ).model_dump(mode="json", by_alias=True)
    return frame


async def _receive(websocket: WebSocket, budget: RateLimiter, session: _Session) -> None:
    """Accept only heartbeat traffic from the client.

    The stream is read-only by design: commands go through the audited HTTP
    pipeline, never through the socket. This task never writes to the socket; it
    records the close code and lets ``stream`` do the closing.
    """
    while True:
        try:
            raw = await websocket.receive_text()
        except _PEER_GONE:
            # A client closing the tab is normal, not an error.
            return

        if not budget.allow("inbound") or len(raw) > _MAX_INBOUND_BYTES:
            session.close_code = _CLOSE_POLICY_VIOLATION
            return

        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            session.close_code = _CLOSE_POLICY_VIOLATION
            return

        if not isinstance(message, dict) or message.get("type") not in _ACCEPTED_INBOUND_TYPES:
            session.close_code = _CLOSE_POLICY_VIOLATION
            return
