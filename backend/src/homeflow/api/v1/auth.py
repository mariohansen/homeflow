"""Client credential endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from homeflow.api.deps import CurrentContainer, CurrentPrincipal
from homeflow.api.schemas import WebSocketTicketResponse

router = APIRouter(prefix="/v1/auth")


@router.post("/ws-ticket", response_model=WebSocketTicketResponse)
def issue_websocket_ticket(
    principal: CurrentPrincipal,
    container: CurrentContainer,
) -> WebSocketTicketResponse:
    """Exchange the client credential for a single-use WebSocket ticket.

    Browsers cannot set an ``Authorization`` header on a WebSocket handshake and
    a credential must never travel in a URL, so the client presents this ticket
    through ``Sec-WebSocket-Protocol`` instead. It expires in seconds and can be
    redeemed once.
    """
    ticket = container.tickets.issue(principal)
    return WebSocketTicketResponse(
        ticket=ticket,
        expires_in_seconds=container.tickets.ttl_seconds,
    )
