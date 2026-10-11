"""Token bucket arithmetic with a fake clock, and the 429 the endpoints answer with."""

from types import SimpleNamespace

from backend.core.ratelimit import TokenBucket, client_key
from backend.tests.conftest import trip_request


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_burst_up_to_capacity_then_refused_with_wait_time():
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, rate=0.1, clock=clock)  # one token every 10s
    assert [bucket.take("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.take("a") == 10.0  # empty: a full token is 10s away


def test_wait_time_shrinks_as_the_bucket_refills():
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, rate=0.1, clock=clock)
    bucket.take("a")
    clock.now += 4
    assert abs(bucket.take("a") - 6.0) < 1e-9  # 0.4 of a token back, 0.6 to go
    clock.now += 6
    assert bucket.take("a") == 0.0


def test_refused_requests_do_not_spend_or_reset_the_refill():
    # Hammering an empty bucket must not push the next allowed request further away.
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, rate=0.1, clock=clock)
    bucket.take("a")
    for _ in range(9):
        clock.now += 1
        assert bucket.take("a") > 0
    clock.now += 1.01
    assert bucket.take("a") == 0.0  # 10s after the first take, not 10s after the last refusal


def test_refill_is_capped_at_capacity():
    clock = FakeClock()
    bucket = TokenBucket(capacity=2, rate=1.0, clock=clock)
    bucket.take("a")
    clock.now += 3600  # an hour idle earns two tokens, not 3600
    assert [bucket.take("a") for _ in range(3)][:2] == [0.0, 0.0]
    assert bucket.take("a") > 0


def test_clients_have_separate_buckets():
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, rate=0.1, clock=clock)
    assert bucket.take("a") == 0.0
    assert bucket.take("a") > 0
    assert bucket.take("b") == 0.0


def test_reset_refills_everyone():
    bucket = TokenBucket(capacity=1, rate=0.1, clock=FakeClock())
    bucket.take("a")
    bucket.reset()
    assert bucket.take("a") == 0.0


def test_number_of_tracked_clients_is_bounded():
    # Memory bound: at max_clients the table is dropped rather than grown.
    bucket = TokenBucket(capacity=1, rate=0.1, clock=FakeClock(), max_clients=3)
    for key in "abcd":
        bucket.take(key)
    assert len(bucket._buckets) <= 3


def test_client_key_uses_first_forwarded_hop_then_socket_peer():
    def request(headers, host="10.0.0.1"):
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host) if host else None)
    assert client_key(request({"x-forwarded-for": "203.0.113.7, 10.0.0.2"})) == "203.0.113.7"
    assert client_key(request({})) == "10.0.0.1"
    assert client_key(request({}, host=None)) == "unknown"


def _fail_fast(monkeypatch):
    """Make the itinerary endpoint answer 400 at once, so only the limiter is under test."""
    from backend.api import routes

    def run_pipeline(req, **kwargs):
        raise ValueError("stub")
    monkeypatch.setattr(routes, "run_pipeline", run_pipeline)


def test_itinerary_endpoint_answers_429_with_retry_after(client, monkeypatch):
    # Six requests in a burst are let through, the seventh is refused and told when to come back.
    _fail_fast(monkeypatch)
    statuses = [client.post("/api/itinerary", json=trip_request()).status_code for _ in range(6)]
    assert statuses == [400] * 6
    r = client.post("/api/itinerary", json=trip_request())
    assert r.status_code == 429
    assert r.json()["detail"] == "Too many requests"
    assert 1 <= int(r.headers["retry-after"]) <= 21  # one token per 20s


def test_stream_endpoint_draws_from_the_same_bucket(client, monkeypatch):
    _fail_fast(monkeypatch)
    for _ in range(6):
        client.post("/api/itinerary", json=trip_request())
    assert client.post("/api/itinerary/stream", json=trip_request()).status_code == 429


def test_limit_is_per_client(client, monkeypatch):
    # One client draining its bucket does not lock out a client behind another address.
    _fail_fast(monkeypatch)
    for _ in range(7):
        last = client.post("/api/itinerary", json=trip_request(), headers={"X-Forwarded-For": "198.51.100.1"})
    assert last.status_code == 429
    other = client.post("/api/itinerary", json=trip_request(), headers={"X-Forwarded-For": "198.51.100.2"})
    assert other.status_code == 400


def test_login_attempts_are_limited(client):
    # Ten guesses, then 429: the limiter runs before the password is even checked.
    creds = {"email": "nobody@example.com", "password": "guess-number-one"}
    statuses = [client.post("/api/auth/login", json=creds).status_code for _ in range(11)]
    assert statuses == [401] * 10 + [429]
