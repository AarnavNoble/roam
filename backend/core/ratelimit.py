"""
Per-client token bucket.

Itinerary generation spends LLM tokens and Overpass quota that are shared by
every user, so one client must not be able to drain them. Each client gets a
bucket of `capacity` tokens refilled at `rate` per second; a request with an
empty bucket is answered 429 with a Retry-After header.

State is in-process, which is correct for the single-replica deployment. With
more replicas this moves to Redis.
"""

import threading
import time
from typing import Callable

from fastapi import HTTPException, Request


class TokenBucket:
    def __init__(self, capacity: float, rate: float, clock: Callable[[], float] = time.monotonic,
                 max_clients: int = 10_000):
        self.capacity = capacity
        self.rate = rate
        self._clock = clock
        self._max_clients = max_clients
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_seen)

    def take(self, key: str) -> float:
        """Take one token. Returns 0 if allowed, else seconds until one is available."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return (1 - tokens) / self.rate
            if len(self._buckets) >= self._max_clients and key not in self._buckets:
                self._buckets.clear()  # crude bound on memory; refills everyone
            self._buckets[key] = (tokens - 1, now)
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


def client_key(request: Request) -> str:
    # Behind the Hugging Face proxy the socket peer is the proxy itself.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limiter(bucket: TokenBucket):
    """FastAPI dependency enforcing `bucket` per client."""
    def dependency(request: Request) -> None:
        retry_after = bucket.take(client_key(request))
        if retry_after > 0:
            raise HTTPException(status_code=429, detail="Too many requests",
                                headers={"Retry-After": str(int(retry_after) + 1)})
    return dependency
