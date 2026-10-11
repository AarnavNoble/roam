"""
Small reliability primitives shared by every upstream client.

The pipeline depends on five services it does not control (Nominatim,
Overpass, OpenRouteService, Wikipedia, the LLM provider). Each of them has
been down or rate-limited at some point, so every call goes through:

  Deadline        one wall-clock budget for the whole request, so a slow stage
                  shortens the timeouts of the stages after it
  retry           bounded attempts with exponential backoff and full jitter
  CircuitBreaker  stop calling an upstream that keeps failing, probe it later
  hedged          race replicas of the same call and take the first success
  TTLCache        serve repeats locally, and (get_stale) serve an expired
                  entry when the upstream that would refresh it is down.
                  Overpass uses both; the geocoder cache only uses get()
"""

import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, TypeVar

T = TypeVar("T")


class UpstreamError(RuntimeError):
    """An upstream call failed after every retry, failover and fallback."""


class DeadlineExceeded(UpstreamError):
    pass


class Deadline:
    """A fixed point in time by which the request must finish."""

    def __init__(self, seconds: float):
        self._expires = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self._expires - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def timeout(self, cap: float) -> float:
        """Timeout for one call: at most `cap`, never past the deadline."""
        left = self.remaining()
        if left <= 0.0:
            raise DeadlineExceeded("request deadline exceeded")
        return min(cap, left)


def retry(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 0.3,
    max_delay: float = 4.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    deadline: Deadline | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """
    Call `fn` up to `attempts` times. The wait before attempt n is drawn
    uniformly from [0, min(max_delay, base_delay * 2**n)] so that clients
    retrying together do not land on the upstream at the same instant.
    """
    last: BaseException | None = None
    for attempt in range(attempts):
        if deadline is not None and deadline.expired():
            break
        try:
            return fn()
        except retry_on as e:
            last = e
            if attempt == attempts - 1:
                break
            delay = random.uniform(0, min(max_delay, base_delay * 2 ** attempt))
            if deadline is not None:
                delay = min(delay, deadline.remaining())
            sleep(delay)
    if last is None:
        raise DeadlineExceeded("request deadline exceeded before first attempt")
    raise last


class CircuitBreaker:
    """
    closed     calls pass through; consecutive failures are counted
    open       calls are refused until `reset_after` seconds have passed
    half-open  one probe call is let through; success closes, failure reopens
    """

    def __init__(self, name: str, failure_threshold: int = 3, reset_after: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_after = reset_after
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if self._clock() - self._opened_at >= self.reset_after:
                return "half-open"
            return "open"

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self.reset_after:
                return False
            if self._probing:
                return False  # someone else is already probing
            self._probing = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probing = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._probing or self._failures >= self.failure_threshold:
                self._opened_at = self._clock()
            self._probing = False

    def abandon(self) -> None:
        """
        An allowed call ended without telling us anything about the upstream
        (we ran out of time, or the request itself was bad). Neither success
        nor failure is recorded, but a half-open probe slot is given back so
        the next caller can probe.
        """
        with self._lock:
            self._probing = False

    def call(self, fn: Callable[[], T]) -> T:
        if not self.allow():
            raise UpstreamError(f"{self.name}: circuit open")
        # BaseException, so that nothing (not even a cancelled thread) can leave
        # a half-open breaker with its probe slot taken forever.
        try:
            result = fn()
        except BaseException:
            self.record_failure()
            raise
        self.record_success()
        return result


def hedged(calls: list[Callable[[], T]], hedge_after: float, timeout: float) -> T:
    """
    Start calls[0]. If it has not answered within `hedge_after` seconds, or it
    fails, start the next one, and so on. Return the first success.

    This trades a little extra load for tail latency: a replica that is slow
    or erroring no longer decides how long the caller waits.
    """
    if not calls:
        raise UpstreamError("no replicas to call")
    deadline = time.monotonic() + timeout
    pool = ThreadPoolExecutor(max_workers=len(calls))
    pending: set = set()
    errors: list[BaseException] = []
    started = 0
    try:
        while True:
            if started < len(calls):
                pending.add(pool.submit(calls[started]))
                started += 1
            elif not pending:
                raise UpstreamError(f"all {len(calls)} replicas failed: {errors[-1]}")
            left = deadline - time.monotonic()
            if left <= 0:
                raise DeadlineExceeded("hedged call timed out")
            # While a spare replica exists, wait only the hedge delay for an answer.
            wait_for = min(hedge_after, left) if started < len(calls) else left
            done, pending = wait(pending, timeout=wait_for, return_when=FIRST_COMPLETED)
            for fut in done:
                if fut.exception() is None:
                    return fut.result()
                errors.append(fut.exception())
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class TTLCache:
    """
    Thread-safe cache with a freshness window and a longer staleness window.

    get()        returns a value only while it is fresh
    get_stale()  returns it until `stale_ttl`, for use when the upstream that
                 would refresh it is down
    """

    def __init__(self, ttl: float, stale_ttl: float | None = None, max_size: int = 1024,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.stale_ttl = stale_ttl if stale_ttl is not None else ttl
        self.max_size = max_size
        self._clock = clock
        self._lock = threading.Lock()
        self._data: dict = {}
        self.hits = 0
        self.misses = 0

    def _get(self, key, max_age: float):
        with self._lock:
            entry = self._data.get(key)
            if entry is None or self._clock() - entry[0] > max_age:
                self.misses += 1
                return None
            self.hits += 1
            return entry[1]

    def get(self, key):
        return self._get(key, self.ttl)

    def get_stale(self, key):
        return self._get(key, self.stale_ttl)

    def set(self, key, value) -> None:
        with self._lock:
            if len(self._data) >= self.max_size and key not in self._data:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[key] = (self._clock(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
