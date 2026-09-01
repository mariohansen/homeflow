"""Request-scoped correlation and conservative response headers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

Handler = Callable[[Request], Awaitable[Response]]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a server-generated correlation id to every request.

    The id is never taken from a client header: an attacker-controlled value
    would end up in logs and in problem details.
    """

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        correlation_id = uuid4().hex
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation_id
        return response


#: The web client is served from this same origin and loads nothing else. A
#: policy this strict is what makes storing a credential in browser storage
#: defensible: no inline script, no third-party code, no eval.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "manifest-src 'self'; "
    "worker-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defensive headers for the API and for the client it serves."""

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if request.url.path.startswith("/v1"):
            # Household state must never sit in a browser or proxy cache.
            response.headers.setdefault("Cache-Control", "no-store")
        else:
            # The client shell may be cached but must always be revalidated,
            # so a deployed fix reaches an installed home-screen app.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response
