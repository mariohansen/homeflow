"""Request dependencies: identity and request budgets.

Being on the private network is not authorisation (see SECURITY.md), so
every route below `/v1` resolves a registered client here first.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, WebSocket

from homeflow.auth.models import Principal
from homeflow.auth.tickets import ticket_from_protocol
from homeflow.container import Container
from homeflow.errors import RateLimitedError, UnauthenticatedError
from homeflow.log import get_logger

_logger = get_logger(__name__)

_BEARER_PREFIX = "Bearer "

#: Subprotocol a browser client offers alongside its ticket, and the one the
#: server echoes back so the ticket value is not repeated in the response.
WEBSOCKET_SUBPROTOCOL = "homeflow.v1"


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_websocket_container(websocket: WebSocket) -> Container:
    return websocket.app.state.container


def _bearer_token(header_value: str | None) -> str | None:
    if not header_value or not header_value.startswith(_BEARER_PREFIX):
        return None
    token = header_value[len(_BEARER_PREFIX) :].strip()
    return token or None


def _peer(request: Request | WebSocket) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


def authenticate(
    connection: Request | WebSocket,
    container: Container,
) -> Principal:
    """Resolve the principal for an HTTP request or a WebSocket handshake.

    Credentials are read from the ``Authorization`` header only: the security
    policy rules out query-string tokens because URLs end up in logs.
    """
    token = _bearer_token(connection.headers.get("authorization"))
    try:
        return container.clients.authenticate(token)
    except UnauthenticatedError:
        # Only failures are counted. Brute forcing produces failures, while a
        # client that reconnects or reloads produces successes, and throttling
        # those locks a household out of its own home.
        _logger.warning("auth.rejected", path=str(connection.url.path))
        if not container.auth_rate_limiter.allow(_peer(connection)):
            raise RateLimitedError("Too many failed authentication attempts.") from None
        raise


def authenticate_websocket(
    websocket: WebSocket,
    container: Container,
) -> tuple[Principal, str | None]:
    """Authenticate a WebSocket handshake and return the subprotocol to echo.

    Native clients send the credential in the ``Authorization`` header. Browsers
    cannot set headers here, so they present a single-use ticket through
    ``Sec-WebSocket-Protocol`` instead — never in the URL, which would be logged.
    """

    def refuse() -> UnauthenticatedError:
        """Count the failure, and say so differently once there are too many."""
        _logger.warning("auth.websocket_rejected")
        if not container.auth_rate_limiter.allow(_peer(websocket)):
            raise RateLimitedError("Too many failed authentication attempts.")
        return UnauthenticatedError()

    token = _bearer_token(websocket.headers.get("authorization"))
    if token is not None:
        try:
            return container.clients.authenticate(token), None
        except UnauthenticatedError:
            raise refuse() from None

    offered = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    for protocol in offered:
        ticket = ticket_from_protocol(protocol)
        if ticket is None:
            continue
        try:
            principal = container.tickets.redeem(ticket)
        except UnauthenticatedError:
            raise refuse() from None
        selected = WEBSOCKET_SUBPROTOCOL if WEBSOCKET_SUBPROTOCOL in offered else protocol
        return principal, selected

    raise refuse()


def require_principal(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> Principal:
    return authenticate(request, container)


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
CurrentContainer = Annotated[Container, Depends(get_container)]


def require_command_budget(
    principal: CurrentPrincipal,
    container: CurrentContainer,
) -> Principal:
    """Bound how fast one client can drive physical devices."""
    if not container.command_rate_limiter.allow(str(principal.client_id)):
        raise RateLimitedError("Too many commands. Slow down and retry shortly.")
    return principal


CommandPrincipal = Annotated[Principal, Depends(require_command_budget)]


def correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else "unknown"


CorrelationId = Annotated[str, Depends(correlation_id)]
