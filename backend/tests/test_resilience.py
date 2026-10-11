import threading
import time

import pytest

from backend.core.resilience import (
    CircuitBreaker, Deadline, DeadlineExceeded, TTLCache, UpstreamError, hedged, retry,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_retry_returns_after_transient_failures():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("boom")
        return "ok"

    assert retry(flaky, attempts=3, sleep=lambda s: None) == "ok"
    assert len(calls) == 3


def test_retry_raises_last_error_when_exhausted():
    def always():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        retry(always, attempts=3, sleep=lambda s: None)


def test_retry_does_not_retry_unlisted_errors():
    calls = []

    def bad_input():
        calls.append(1)
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        retry(bad_input, attempts=3, retry_on=(ConnectionError,), sleep=lambda s: None)
    assert len(calls) == 1


def test_retry_backoff_is_bounded_and_jittered():
    delays = []

    def always():
        raise ConnectionError

    with pytest.raises(ConnectionError):
        retry(always, attempts=5, base_delay=1.0, max_delay=4.0, sleep=delays.append)
    assert len(delays) == 4
    assert all(0 <= d <= cap for d, cap in zip(delays, [1.0, 2.0, 4.0, 4.0]))


def test_retry_stops_at_deadline():
    calls = []

    def always():
        calls.append(1)
        raise ConnectionError

    with pytest.raises(DeadlineExceeded):
        retry(always, attempts=5, deadline=Deadline(0), sleep=lambda s: None)
    assert calls == []


def test_deadline_caps_timeouts():
    d = Deadline(0.5)
    assert d.timeout(10) <= 0.5
    assert d.timeout(0.1) == 0.1
    with pytest.raises(DeadlineExceeded):
        Deadline(0).timeout(10)


def test_breaker_opens_after_threshold_and_refuses_calls():
    clock = FakeClock()
    breaker = CircuitBreaker("x", failure_threshold=2, reset_after=30, clock=clock)

    def fail():
        raise ConnectionError

    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(fail)
    assert breaker.state == "open"
    with pytest.raises(UpstreamError):
        breaker.call(lambda: "never runs")


def test_breaker_half_open_probe_closes_on_success():
    clock = FakeClock()
    breaker = CircuitBreaker("x", failure_threshold=1, reset_after=30, clock=clock)
    breaker.record_failure()
    clock.now = 31
    assert breaker.state == "half-open"
    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.state == "closed"


def test_breaker_half_open_allows_one_probe_and_reopens_on_failure():
    clock = FakeClock()
    breaker = CircuitBreaker("x", failure_threshold=1, reset_after=30, clock=clock)
    breaker.record_failure()
    clock.now = 31
    assert breaker.allow() is True
    assert breaker.allow() is False  # second caller is refused while probing
    breaker.record_failure()
    assert breaker.state == "open"


def test_hedged_takes_fast_replica_when_first_is_slow():
    release = threading.Event()

    def slow():
        release.wait(5)
        return "slow"

    start = time.monotonic()
    assert hedged([slow, lambda: "fast"], hedge_after=0.05, timeout=5) == "fast"
    assert time.monotonic() - start < 1
    release.set()


def test_hedged_fails_over_immediately_on_error():
    def broken():
        raise ConnectionError("503")

    start = time.monotonic()
    assert hedged([broken, lambda: "ok"], hedge_after=10, timeout=5) == "ok"
    assert time.monotonic() - start < 1  # did not sit out the hedge delay


def test_hedged_does_not_start_spares_when_first_is_fast():
    started = []

    def spare():
        started.append(1)
        return "spare"

    assert hedged([lambda: "first", spare], hedge_after=1, timeout=5) == "first"
    assert started == []


def test_hedged_raises_when_all_replicas_fail():
    def broken():
        raise ConnectionError

    with pytest.raises(UpstreamError):
        hedged([broken, broken, broken], hedge_after=0.01, timeout=5)


def test_cache_fresh_then_stale_then_gone():
    clock = FakeClock()
    cache = TTLCache(ttl=10, stale_ttl=100, clock=clock)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    clock.now = 11
    assert cache.get("k") is None
    assert cache.get_stale("k") == "v"
    clock.now = 101
    assert cache.get_stale("k") is None


def test_cache_evicts_oldest_at_capacity():
    clock = FakeClock()
    cache = TTLCache(ttl=10, max_size=2, clock=clock)
    cache.set("a", 1)
    clock.now = 1
    cache.set("b", 2)
    clock.now = 2
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2 and cache.get("c") == 3
