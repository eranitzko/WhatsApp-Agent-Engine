"""Per-group sliding-window rate limiter.

Two limits enforced independently:
  - Text messages: 20 per group per minute  (anti-spam / agent cost)
  - Image messages: 10 per group per hour   (Gemini API cost control)

Usage:
    limiter = RateLimiter()
    if not limiter.allow(group_id, "text"):
        # drop or reply with a throttle message
        ...
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class _SlidingWindow:
    """Thread-safe sliding window counter."""

    def __init__(self, max_count: int, window_seconds: float):
        self.max_count = max_count
        self.window    = window_seconds
        self._lock     = Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            q = self._buckets[key]
            # Evict expired timestamps
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_count:
                return False
            q.append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
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

    def allow_text(self, group_id: str) -> bool:
        return self._text.allow(group_id)

    def allow_image(self, group_id: str) -> bool:
        return self._image.allow(group_id)

    def text_remaining(self, group_id: str) -> int:
        return self._text.remaining(group_id)

    def image_remaining(self, group_id: str) -> int:
        return self._image.remaining(group_id)


# Singleton used by main.py
rate_limiter = RateLimiter()
