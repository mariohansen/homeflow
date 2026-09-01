"""Short-lived, single-use tickets for WebSocket handshakes.

A browser cannot set an ``Authorization`` header on a WebSocket handshake, and
the security policy rules out query-string credentials: URLs get logged.
The client therefore exchanges its long-lived credential — over the normal
authenticated HTTP API — for a ticket it presents through
``Sec-WebSocket-Protocol``.

The exchange only narrows exposure if the ticket is genuinely weak on its own:
it is single-use, expires in seconds, is stored as a digest, and is compared in
constant time.
"""

from __future__ import annotations

import hashlib
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeflow.auth.models import Principal
from homeflow.clock import Clock
from homeflow.errors import UnauthenticatedError

TICKET_TTL_SECONDS = 30
_MAX_OUTSTANDING = 64
_PROTOCOL_PREFIX = "homeflow.ticket."


@dataclass(frozen=True, slots=True)
class _Issued:
    principal: Principal
    expires_at: datetime


def ticket_protocol(ticket: str) -> str:
    """Render a ticket as the WebSocket subprotocol the client offers."""
    return f"{_PROTOCOL_PREFIX}{ticket}"


def ticket_from_protocol(protocol: str) -> str | None:
    if not protocol.startswith(_PROTOCOL_PREFIX):
        return None
    return protocol[len(_PROTOCOL_PREFIX) :] or None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TicketStore:
    """Bounded store of outstanding WebSocket tickets."""

    __slots__ = ("_clock", "_tickets", "_ttl")

    def __init__(self, clock: Clock, *, ttl_seconds: int = TICKET_TTL_SECONDS) -> None:
        self._clock = clock
        self._ttl = timedelta(seconds=ttl_seconds)
        self._tickets: OrderedDict[str, _Issued] = OrderedDict()

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, principal: Principal) -> str:
        now = self._clock.now()
        self._prune(now)
        ticket = secrets.token_urlsafe(32)
        if len(self._tickets) >= _MAX_OUTSTANDING:
            self._tickets.popitem(last=False)
        self._tickets[_digest(ticket)] = _Issued(
            principal=principal,
            expires_at=now + self._ttl,
        )
        return ticket

    def redeem(self, ticket: str | None) -> Principal:
        """Consume a ticket exactly once, or raise ``UnauthenticatedError``."""
        now = self._clock.now()
        self._prune(now)

        # Always hash, and always walk every candidate, so that timing does not
        # distinguish "no such ticket" from "expired" or "none outstanding".
        digest = _digest(ticket or "")
        found: _Issued | None = None
        found_key: str | None = None
        for key, issued in self._tickets.items():
            if secrets.compare_digest(digest, key):
                found, found_key = issued, key

        if found is None or found_key is None or not ticket:
            raise UnauthenticatedError("The WebSocket ticket is invalid or has expired.")

        del self._tickets[found_key]
        if found.expires_at <= now:  # pragma: no cover - pruned above, kept as a guard
            raise UnauthenticatedError("The WebSocket ticket is invalid or has expired.")
        return found.principal

    def _prune(self, now: datetime) -> None:
        expired = [key for key, issued in self._tickets.items() if issued.expires_at <= now]
        for key in expired:
            del self._tickets[key]
