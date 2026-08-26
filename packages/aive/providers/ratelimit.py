"""Per-model request pacing.

A free tier is defined by its rate limit more than by its price. Analysing one
project sends hundreds of requests, so without pacing a run does not merely go
slower - it dies part way through with 429s, having already spent quota on the
segments it did finish.

The limiter is keyed by model, not by provider instance: the pipeline creates
providers freely, and they must all draw on the same budget.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable


class RateLimiter:
    """Sliding-window limiter: at most ``rpm`` starts in any 60 seconds.

    The clock and the sleep are injectable so this can be tested against a
    fake clock. Patching ``asyncio.sleep`` globally would be both slow and
    liable to affect unrelated code in the same process.
    """

    def __init__(
        self,
        requests_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.rpm = max(1, requests_per_minute)
        self._starts: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep or asyncio.sleep

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = self._clock()
                while self._starts and now - self._starts[0] >= 60.0:
                    self._starts.popleft()
                if len(self._starts) < self.rpm:
                    self._starts.append(now)
                    return
                # Wait until the oldest start ages out of the window.
                wait = 60.0 - (now - self._starts[0]) + 0.01
            await self._sleep(wait)

    def penalise(self, seconds: float) -> None:
        """Record a server-side rejection so the next calls back off too.

        A 429 means the real limit is lower than configured, so the window is
        filled artificially rather than only delaying the one caller that was
        unlucky enough to hit it.
        """
        now = self._clock()
        for _ in range(self.rpm):
            self._starts.append(now + max(0.0, seconds - 60.0))


_LIMITERS: dict[str, RateLimiter] = {}


def limiter_for(model_key: str, requests_per_minute: int | None) -> RateLimiter | None:
    if not requests_per_minute:
        return None
    existing = _LIMITERS.get(model_key)
    if existing is None or existing.rpm != requests_per_minute:
        existing = RateLimiter(requests_per_minute)
        _LIMITERS[model_key] = existing
    return existing


def reset() -> None:
    """Test helper: forget every window."""
    _LIMITERS.clear()
