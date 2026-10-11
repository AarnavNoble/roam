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

from backend import config


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
            # pop and re-insert, so the dict stays ordered least recently seen first
            tokens, last = self._buckets.pop(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            while len(self._buckets) >= self._max_clients:
                del self._buckets[next(iter(self._buckets))]  # bound memory: evict the stalest client
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return (1 - tokens) / self.rate
            self._buckets[key] = (tokens - 1, now)
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


def client_key(request: Request) -> str:
    """
    The address to rate-limit on. Behind a proxy the socket peer is the proxy,
    so the address comes from X-Forwarded-For. Each proxy appends the peer it
    saw, so only the last TRUSTED_PROXIES entries were written by our own
    infrastructure. The leftmost entry is whatever the client sent and must not
    be used: a client could pick a new one per request and never be limited.
    """
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    n = config.TRUSTED_PROXIES
    if n > 0 and len(hops) >= n:
        return hops[-n]
    return request.client.host if request.client else "unknown"


def limiter(bucket: TokenBucket):
    """FastAPI dependency enforcing `bucket` per client."""
    def dependency(request: Request) -> None:
        retry_after = bucket.take(client_key(request))
        if retry_after > 0:
            raise HTTPException(status_code=429, detail="Too many requests",
                                headers={"Retry-After": str(int(retry_after) + 1)})
    return dependency
