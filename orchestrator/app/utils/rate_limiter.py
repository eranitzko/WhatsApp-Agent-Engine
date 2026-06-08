"""Per-group sliding-window rate limiter.

Two limits enforced independently:
  - Text messages: 20 per group per minute  (anti-spam / agent cost)
  - Image messages: 10 per group per hour   (Gemini API cost control)

Usage:
    limiter = RateLimiter()
    if not await limiter.allow_text(group_id):
        # drop or reply with a throttle message
        ...
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class _SlidingWindow:
    """Async sliding window counter."""

    def __init__(self, max_count: int, window_seconds: float):
        self.max_count = max_count
        self.window    = window_seconds
        self._lock     = asyncio.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        async with self._lock:
            q = self._buckets[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_count:
                return False
            q.append(now)
            return True

    async def remaining(self, key: str) -> int:
        now = time.monotonic()
        cutoff = now - self.window
        async with self._lock:
            q = self._buckets[key]
            while q and q[0] < cutoff:
                q.popleft()
            return max(0, self.max_count - len(q))


class RateLimiter:
    """Composite rate limiter with separate windows for text and image events."""

    def __init__(
        self,
        text_per_minute: int = 20,
        images_per_hour: int = 10,
    ):
        self._text  = _SlidingWindow(text_per_minute, 60)
        self._image = _SlidingWindow(images_per_hour, 3600)

    async def allow_text(self, group_id: str) -> bool:
        return await self._text.allow(group_id)

    async def allow_image(self, group_id: str) -> bool:
        return await self._image.allow(group_id)

    async def text_remaining(self, group_id: str) -> int:
        return await self._text.remaining(group_id)

    async def image_remaining(self, group_id: str) -> int:
        return await self._image.remaining(group_id)


# Singleton used by main.py
rate_limiter = RateLimiter()
