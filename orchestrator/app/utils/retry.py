"""Async retry with exponential backoff + jitter.

Usage:
    result = await retry(my_async_fn, arg1, arg2, retries=3, base_delay=1.0)

Or as a decorator:
    @with_retry(retries=3, base_delay=1.0)
    async def my_fn(): ...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Exception types that are worth retrying (transient failures)
_RETRYABLE = (
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
)

# If google-generativeai or httpx raise these, we also retry
try:
    import httpx
    _RETRYABLE = (*_RETRYABLE, httpx.TimeoutException, httpx.NetworkError)
except ImportError:
    pass


def _is_rate_limit(exc: BaseException) -> bool:
    """Return True if this is a 429 rate-limit error (do NOT retry these)."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return True
    code = getattr(exc, "code", None)
    if code == 429:
        return True
    # google.api_core.exceptions.ResourceExhausted is the Gemini 429
    return type(exc).__name__ == "ResourceExhausted"


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient errors; False for permanent or rate-limit failures."""
    if _is_rate_limit(exc):
        return False  # retrying 429s immediately just burns more quota
    if isinstance(exc, _RETRYABLE):
        return True
    # Retry on 5xx server errors only
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None and status >= 500:
        return True
    code = getattr(exc, "code", None)
    if code is not None and code >= 500:
        return True
    return False


async def retry(
    fn: Callable[..., T],
    *args,
    retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs,
) -> T:
    """Call `fn(*args, **kwargs)`, retrying on transient errors.

    Uses full jitter exponential backoff:
        delay = random(0, min(max_delay, base_delay * 2 ** attempt))
    """
    last_exc: BaseException | None = None

    for attempt in range(retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt == retries or not _is_retryable(exc):
                raise
            cap = min(max_delay, base_delay * (2 ** attempt))
            delay = random.uniform(0, cap)
            logger.warning(
                "Retryable error on attempt %d/%d: %s — retrying in %.1fs",
                attempt + 1, retries, exc, delay,
            )
            await asyncio.sleep(delay)

    raise last_exc  # unreachable, but satisfies type checkers


def with_retry(retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Decorator that wraps an async function with retry logic."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            return await retry(fn, *args, retries=retries, base_delay=base_delay, max_delay=max_delay, **kwargs)
        return wrapper
    return decorator
