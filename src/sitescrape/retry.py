"""Exponential-backoff retry strategy.

Provides a retry decorator and a synchronous retry helper for use
throughout the scraper so transient failures are handled automatically.
"""

from __future__ import annotations

import asyncio
import random
import time
import functools
from typing import Any, Callable, TypeVar

from sitescrape.logger import get_logger

logger = get_logger()

F = TypeVar("F", bound=Callable[..., Any])

# ------------------------------------------------------------------
# Synchronous retry decorator
# ------------------------------------------------------------------


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> Callable[[F], F]:
    """Decorator: retry a function on transient failures.

    Parameters
    ----------
    max_attempts:
        Total number of attempts (1 initial + retries).
    base_delay:
        Initial delay in seconds between attempts.
    max_delay:
        Cap on delay so we don't wait forever.
    backoff_factor:
        Each subsequent attempt multiplies the delay by this value.
    jitter:
        Add random jitter (±25%) to avoid thundering-herd.
    retryable_exceptions:
        Tuple of exception types to retry on. Defaults to
        *(ConnectionError, TimeoutError, OSError)*.
    """

    if retryable_exceptions is None:
        retryable_exceptions = (ConnectionError, TimeoutError, OSError)

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
            attempt = 0
            delay = base_delay
            last_exc: Exception | None = None

            while attempt < max_attempts:
                attempt += 1
                try:
                    return fn(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exc = exc
                    if attempt >= max_attempts:
                        break

                    if jitter:
                        delay = delay * backoff_factor * (
                            0.75 + random.random() * 0.5
                        )
                    else:
                        delay = min(delay * backoff_factor, max_delay)

                    logger.warning(
                        "Retryable error, attempt %d/%d — %s: %s. "
                        "Retrying in %.1fs …",
                        attempt, max_attempts, type(exc).__name__,
                        exc, delay,
                        extra={"ctx": fn.__name__, "retry": attempt},
                    )
                    time.sleep(delay)

            # All attempts exhausted — raise the last exception
            if last_exc:
                raise last_exc

            return fn(*args, **kwargs)  # pragma: no cover

        wrapper._retry_config = {  # type: ignore[attr-defined]
            "max_attempts": max_attempts,
            "base_delay": base_delay,
            "max_delay": max_delay,
            "backoff_factor": backoff_factor,
            "jitter": jitter,
        }
        return wrapper  # type: ignore[return-value]

    return decorator


# ------------------------------------------------------------------
# Async-friendly retry helper
# ------------------------------------------------------------------


async def arun_with_retry(
    fn: Callable[..., Any],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
    **kwargs: Any,
) -> Any:
    """Run a function with exponential-backoff retry (async-compatible).

    Unlike the decorator, this is a function so it can be used with
    async callables or sync callables alike.
    """
    if retryable_exceptions is None:
        retryable_exceptions = (ConnectionError, TimeoutError, OSError)

    attempt = 0
    delay = base_delay
    last_exc: Exception | None = None

    while attempt < max_attempts:
        attempt += 1
        try:
            result = fn(*args, **kwargs)
            # Handle coroutines
            if asyncio.iscoroutine(result):
                return await result
            return result
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break
            if jitter:
                delay = delay * backoff_factor * (0.75 + random.random() * 0.5)
            else:
                delay = min(delay * backoff_factor, max_delay)

            logger.warning(
                "Retryable error, attempt %d/%d — %s: %s. "
                "Retrying in %.1fs …",
                attempt, max_attempts, type(exc).__name__,
                exc, delay,
                extra={"ctx": fn.__name__ if hasattr(fn, "__name__") else str(fn),
                       "retry": attempt},
            )
            if asyncio.iscoroutinefunction(fn):
                await asyncio.sleep(delay)
            else:
                time.sleep(delay)

    if last_exc:
        raise last_exc

    return fn(*args, **kwargs)  # pragma: no cover


# ------------------------------------------------------------------
# Rate limiter
# ------------------------------------------------------------------

class RateLimiter:
    """Simple token-bucket rate limiter for outgoing requests."""

    def __init__(
        self,
        rate: float = 0.5,  # requests per second
        burst: int | None = None,
    ) -> None:
        """
        Parameters
        ----------
        rate:
            Sustained requests per second.
        burst:
            Maximum burst size (defaults to *rate*).
        """
        self.rate = rate
        self.burst = burst if burst is not None else int(rate)
        self._tokens: float = float(self.burst)
        self._last_time: float = time.monotonic()
        self._lock: asyncio.Lock | None = None

        try:
            import threading
            self._lock = threading.Lock()
        except ImportError:
            pass

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_time
        self._last_time = now
        self._tokens = min(
            float(self.burst),
            self._tokens + elapsed * self.rate,
        )

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        if self._lock:
            with self._lock:
                self._refill()
                while self._tokens < 1:
                    wait = (1 - self._tokens) / self.rate
                    time.sleep(wait)
                    self._refill()
                self._tokens -= 1
        else:
            self._refill()
            while self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                time.sleep(wait)
                self._refill()
            self._tokens -= 1
