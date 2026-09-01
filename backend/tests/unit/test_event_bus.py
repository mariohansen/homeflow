"""The bus must stay bounded and must admit when it dropped events."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import run
from homeflow.events.bus import EventBus
from homeflow.events.models import DomainEvent, EventType


def _event(index: int) -> DomainEvent:
    return DomainEvent(
        type=EventType.DEVICE_STATE_CHANGED,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload={"index": index},
    )


def test_subscriber_receives_published_events() -> None:
    async def scenario() -> list[int]:
        bus = EventBus(queue_size=8)
        with bus.subscribe() as subscription:
            bus.publish(_event(1))
            bus.publish(_event(2))
            first = await subscription.__anext__()
            second = await subscription.__anext__()
        return [first.payload["index"], second.payload["index"]]

    assert run(scenario()) == [1, 2]


def test_overflow_drops_oldest_and_flags_lag() -> None:
    async def scenario() -> tuple[bool, int]:
        bus = EventBus(queue_size=2)
        with bus.subscribe() as subscription:
            for index in range(5):
                bus.publish(_event(index))
            lagged = subscription.lagged
            newest_kept = await subscription.__anext__()
        return lagged, newest_kept.payload["index"]

    lagged, kept = run(scenario())
    assert lagged is True
    # The oldest events were discarded, not the newest.
    assert kept == 3


def test_take_lagged_clears_the_flag() -> None:
    async def scenario() -> tuple[bool, bool]:
        bus = EventBus(queue_size=1)
        with bus.subscribe() as subscription:
            bus.publish(_event(1))
            bus.publish(_event(2))
            return subscription.take_lagged(), subscription.take_lagged()

    assert run(scenario()) == (True, False)


def test_closing_a_subscription_unsubscribes_it() -> None:
    async def scenario() -> int:
        bus = EventBus()
        subscription = bus.subscribe()
        assert bus.subscriber_count == 1
        subscription.close()
        return bus.subscriber_count

    assert run(scenario()) == 0


def test_publishing_without_subscribers_is_harmless() -> None:
    bus = EventBus()
    bus.publish(_event(1))
    assert bus.subscriber_count == 0
