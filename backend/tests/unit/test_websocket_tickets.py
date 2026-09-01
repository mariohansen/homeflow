"""WebSocket tickets exist to be weak on their own: short-lived and single-use."""

from __future__ import annotations

from uuid import uuid4

import pytest

from homeflow.auth.models import Principal
from homeflow.auth.tickets import TicketStore, ticket_from_protocol, ticket_protocol
from homeflow.clock import ManualClock
from homeflow.errors import UnauthenticatedError

PRINCIPAL = Principal(user_id=uuid4(), client_id=uuid4(), display_name="Alice")


def test_a_ticket_can_be_redeemed_once() -> None:
    store = TicketStore(ManualClock())
    ticket = store.issue(PRINCIPAL)

    assert store.redeem(ticket) == PRINCIPAL
    with pytest.raises(UnauthenticatedError):
        store.redeem(ticket)


def test_a_ticket_expires() -> None:
    clock = ManualClock()
    store = TicketStore(clock, ttl_seconds=30)
    ticket = store.issue(PRINCIPAL)

    clock.advance(31)
    with pytest.raises(UnauthenticatedError):
        store.redeem(ticket)


def test_unknown_and_empty_tickets_are_rejected() -> None:
    store = TicketStore(ManualClock())
    store.issue(PRINCIPAL)

    for candidate in ("not-a-ticket", "", None):
        with pytest.raises(UnauthenticatedError):
            store.redeem(candidate)


def test_tickets_are_unique() -> None:
    store = TicketStore(ManualClock())
    assert store.issue(PRINCIPAL) != store.issue(PRINCIPAL)


def test_the_outstanding_set_stays_bounded() -> None:
    store = TicketStore(ManualClock())
    tickets = [store.issue(PRINCIPAL) for _ in range(200)]

    # Older tickets are evicted rather than accumulating without limit.
    redeemable = 0
    for ticket in tickets:
        try:
            store.redeem(ticket)
        except UnauthenticatedError:
            continue
        redeemable += 1
    assert 0 < redeemable <= 64


def test_protocol_round_trip() -> None:
    assert ticket_from_protocol(ticket_protocol("abc")) == "abc"
    assert ticket_from_protocol("homeflow.v1") is None
    assert ticket_from_protocol("homeflow.ticket.") is None
