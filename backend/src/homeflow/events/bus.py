"""Bounded in-process publish/subscribe.

A slow or stalled subscriber must never grow memory without limit and must never
block a device adapter, so each subscriber owns a bounded queue. When it
overflows the oldest event is dropped and the subscriber is flagged as lagged;
the WebSocket layer turns that flag into a resync hint so a client can never
silently diverge from real device state (see docs/architecture/overview.md).
"""

from __future__ import annotations

import asyncio
import contextlib
from types import TracebackType
from typing import Self

from homeflow.events.models import DomainEvent


class Subscription:
    """Async iterator over events published after subscription."""

    __slots__ = ("_bus", "_closed", "_lagged", "_queue")

    def __init__(self, bus: EventBus, maxsize: int) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[DomainEvent | None] = asyncio.Queue(maxsize=maxsize)
        self._lagged = False
        self._closed = False

    @property
    def lagged(self) -> bool:
        return self._lagged

    def take_lagged(self) -> bool:
        """Return the lag flag and clear it."""
        was_lagged, self._lagged = self._lagged, False
        return was_lagged

    def offer(self, event: DomainEvent) -> None:
        """Enqueue without blocking. Called by the bus only."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._lagged = True
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(event)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus.unsubscribe(self)
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> DomainEvent:
        event = await self._queue.get()
        if event is None:
            raise StopAsyncIteration
        return event


class EventBus:
    """Fan-out to every live subscriber. Publishing never awaits."""

    __slots__ = ("_queue_size", "_subscribers")

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: list[Subscription] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> Subscription:
        subscription = Subscription(self, self._queue_size)
        self._subscribers.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with contextlib.suppress(ValueError):
            self._subscribers.remove(subscription)

    def publish(self, event: DomainEvent) -> None:
        for subscriber in tuple(self._subscribers):
            subscriber.offer(event)
