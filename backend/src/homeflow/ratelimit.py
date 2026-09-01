"""Token-bucket rate limiting (CLAUDE.md section 70).

Applied to authentication and command submission so that a looping client, a
stuck retry or a hostile process on the private network cannot turn one bug into
a flood of physical device commands.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from homeflow.clock import Clock


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """Per-key token bucket with a bounded number of tracked keys."""

    __slots__ = ("_buckets", "_burst", "_clock", "_max_keys", "_rate_per_second")

    def __init__(
        self,
        *,
        rate_per_minute: int,
        clock: Clock,
        burst: int | None = None,
        max_keys: int = 1024,
    ) -> None:
        self._rate_per_second = rate_per_minute / 60.0
        self._burst = float(burst if burst is not None else rate_per_minute)
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def allow(self, key: str) -> bool:
        now = self._clock.now().timestamp()
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self._max_keys:
                self._buckets.popitem(last=False)
            bucket = _Bucket(tokens=self._burst, updated_at=now)
            self._buckets[key] = bucket
        else:
            self._buckets.move_to_end(key)
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate_per_second)
            bucket.updated_at = now

        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True
