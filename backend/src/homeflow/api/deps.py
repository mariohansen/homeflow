"""Request dependencies: identity and request budgets.

Being on the private network is not authorisation (CLAUDE.md section 13), so
every route below `/v1` resolves a registered client here first.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, WebSocket

from homeflow.auth.models import Principal
from homeflow.container import Container
from homeflow.errors import RateLimitedError, UnauthenticatedError
from homeflow.log import get_logger

_logger = get_logger(__name__)

_BEARER_PREFIX = "Bearer "


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

    Credentials are read from the ``Authorization`` header only: CLAUDE.md
    section 43 rules out query-string tokens because URLs end up in logs.
    """
    if not container.auth_rate_limiter.allow(_peer(connection)):
        raise RateLimitedError("Too many authentication attempts.")

    token = _bearer_token(connection.headers.get("authorization"))
    try:
        return container.clients.authenticate(token)
    except UnauthenticatedError:
        _logger.warning("auth.rejected", path=str(connection.url.path))
        raise


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
