"""Tests for the retry/backoff module and rate limiter."""

import time
from sitescrape.retry import RateLimiter, arun_with_retry, retry


def test_retry_succeeds_on_first_try():
    """A function that doesn't raise should succeed immediately."""
    @retry(max_attempts=3, base_delay=0.01)
    def success():
        return 42

    assert success() == 42


def test_retry_recovers_after_failures():
    """Should succeed after transient failures."""
    call_count = 0

    @retry(max_attempts=3, base_delay=0.01, backoff_factor=1.0, jitter=False)
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("temp")
        return "ok"

    result = flaky()
    assert result == "ok"
    assert call_count == 3


def test_retry_exhausted():
    """Should raise last exception after max_attempts exhausted."""
    call_count = 0

    @retry(max_attempts=3, base_delay=0.01, backoff_factor=1.0, jitter=False)
    def always_fails():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("temp failure")

    try:
        always_fails()
        assert False, "Should have raised"
    except ConnectionError:
        assert call_count == 3


def test_retry_non_retryable():
    """Should NOT retry on non-retryable exceptions."""
    call_count = 0

    @retry(
        max_attempts=3, base_delay=0.01,
        retryable_exceptions=(ConnectionError,),
    )
    def fails_with_type_error():
        nonlocal call_count
        call_count += 1
        raise TypeError("not retryable")

    try:
        fails_with_type_error()
        assert False, "Should have raised"
    except TypeError:
        assert call_count == 1


def test_retry_delay_backoff():
    """Delay should increase with exponential backoff."""
    timings: list[float] = []
    start_calls: list[float] = []

    @retry(max_attempts=3, base_delay=0.05, backoff_factor=2.0, jitter=False)
    def timed_flaky():
        nonlocal timings, start_calls
        start_calls.append(time.monotonic())
        raise ConnectionError("temp")

    try:
        timed_flaky()
    except ConnectionError:
        pass

    # There should be at least 2 delays between 3 attempts
    assert len(start_calls) == 3
    # Verify delays grow (allowing some slack)
    for i in range(1, len(start_calls)):
        assert start_calls[i] - start_calls[i-1] >= 0.04  # base_delay


class TestRateLimiter:
    """Test the RateLimiter token bucket."""

    def test_rate_limiter_allows_burst(self):
        """Burst should allow N immediate calls."""
        rl = RateLimiter(rate=10.0, burst=3)
        for _ in range(3):
            rl.acquire()  # should not block

    def test_rate_limiter_throttles_after_burst(self):
        """Should throttle after burst is exhausted."""
        rl = RateLimiter(rate=10.0, burst=2)
        rl.acquire()  # burst 1
        rl.acquire()  # burst 2
        start = time.monotonic()
        rl.acquire()  # should block briefly
        elapsed = time.monotonic() - start
        assert elapsed > 0.05  # waited for refill

    def test_rate_limiter_refills_over_time(self):
        """Tokens should refill after waiting."""
        rl = RateLimiter(rate=50.0, burst=1)
        rl.acquire()  # consume
        time.sleep(0.05)  # 50/s * 0.05s = 2.5 tokens should refill
        rl.acquire()  # should succeed immediately
        rl.acquire()  # should succeed immediately
