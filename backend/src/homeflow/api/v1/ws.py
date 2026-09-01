"""Authenticated live state stream (CLAUDE.md sections 30 and 43).

The socket carries normalised events only. Clients take an initial REST snapshot
and then follow this stream; if the server ever has to drop events for a slow
consumer it says so explicitly with a resync frame rather than letting the client
believe it is in sync.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from homeflow.api.deps import authenticate, get_websocket_container
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


@router.websocket("/ws")
async def stream(websocket: WebSocket) -> None:
    container = get_websocket_container(websocket)
    try:
        principal = authenticate(websocket, container)
    except HomeFlowError:
        await websocket.close(code=_CLOSE_POLICY_VIOLATION)
        return

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "Hello",
            "protocolVersion": PROTOCOL_VERSION,
            "serverTime": container.clock.now().isoformat(),
            "demoMode": container.settings.demo_mode,
        }
    )
    _logger.info("ws.connected", client_id=str(principal.client_id))

    inbound_budget = RateLimiter(
        rate_per_minute=container.settings.websocket_rate_limit_per_minute,
        clock=container.clock,
    )

    with container.bus.subscribe() as subscription:
        sender = asyncio.create_task(_forward(websocket, subscription, container))
        receiver = asyncio.create_task(_receive(websocket, inbound_budget))
        done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                task.result()

    _logger.info("ws.disconnected", client_id=str(principal.client_id))


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
            await websocket.send_json(
                {"type": "Ping", "serverTime": container.clock.now().isoformat()}
            )
            continue
        except StopAsyncIteration:
            return

        if subscription.take_lagged():
            # Events were dropped for this connection: the client must refetch
            # the REST snapshot instead of trusting its incremental view.
            await websocket.send_json({"type": "ResyncRequired"})

        await websocket.send_json(_frame(event, container))


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


async def _receive(websocket: WebSocket, budget: RateLimiter) -> None:
    """Accept only heartbeat traffic from the client.

    The stream is read-only by design: commands go through the audited HTTP
    pipeline, never through the socket.
    """
    while True:
        raw = await websocket.receive_text()
        if not budget.allow("inbound"):
            await websocket.close(code=_CLOSE_POLICY_VIOLATION)
            return
        if len(raw) > _MAX_INBOUND_BYTES:
            await websocket.close(code=_CLOSE_POLICY_VIOLATION)
            return
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.close(code=_CLOSE_POLICY_VIOLATION)
            return
        if not isinstance(message, dict) or message.get("type") not in _ACCEPTED_INBOUND_TYPES:
            await websocket.close(code=_CLOSE_POLICY_VIOLATION)
            return
