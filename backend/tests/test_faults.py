"""
Fault injection through the real HTTP endpoint.

Every test sends POST /api/itinerary with all upstreams faked (see the
`upstreams` fixture in conftest.py), breaks one thing, and checks three things:
the status code, the exact `degraded` list, and that the itinerary is still
complete. `degraded` is compared with ==, so a fault that silently raises a
second flag (or none) fails the test.

Tests whose comment starts with "Control" inject nothing. They pin the healthy
behaviour that the fault next to them is measured against.
"""

import json
import re
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from backend import config, pipeline
from backend.api import routes
from backend.db.models import PoiCache, Trip
from backend.db.session import engine, session_scope
from backend.ml.optimizer import scheduler
from backend.services import llm, nominatim, overpass
from backend.tests.conftest import (
    MIRROR_A, MIRROR_B, MIRROR_C, N_STOPS, START, llm_error, model_answer, overpass_elements, trip_request,
)


def post(client, **overrides):
    return client.post("/api/itinerary", json=trip_request(**overrides))


def stops_of(body: dict) -> list[dict]:
    return [stop for day in body["days"] for stop in day["stops"]]


def assert_complete(body: dict, n_stops: int = N_STOPS) -> list[dict]:
    """
    Every routed stop is in the itinerary with prose, coordinates and a time.
    When the trip was stored, the stops must also equal the stored route (the
    solver's output) id for id, in order, with the route's coordinates and times.
    """
    stops = stops_of(body)
    assert len(stops) == n_stops
    for stop in stops:
        assert stop["description"].strip()
        assert isinstance(stop["lat"], float) and isinstance(stop["lon"], float)
        assert re.fullmatch(r"\d\d:\d\d", stop["arrival_time"])
    if body.get("trip_id") and n_stops == N_STOPS:
        with session_scope() as db:
            route = db.get(Trip, body["trip_id"]).route
        routed = [(p["id"], p["lat"], p["lon"], p["arrival_time"]) for day in route for p in day["pois"]]
        assert [(s["id"], s["lat"], s["lon"], s["arrival_time"]) for s in stops] == routed
    return stops


def written_by_model(stop: dict) -> bool:
    return stop["description"].startswith("Model prose")


@contextmanager
def database_down():
    """Every SQL statement fails, as it would with the database unreachable."""
    def refuse(conn, cursor, statement, parameters, context, executemany):
        raise OperationalError(statement, parameters, ConnectionError("connection refused"))
    event.listen(engine, "before_cursor_execute", refuse)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", refuse)


def store_stale_pois() -> None:
    """Put a copy of the default POI answer in poi_cache, as an earlier request would have."""
    key = overpass._cache_key(*START, pipeline.MOBILITY_RADIUS["moderate"], ["food", "culture"])
    with session_scope() as db:
        db.add(PoiCache(key=key, payload=overpass_elements(), fetched_at=time.time()))


# ── Control ──────────────────────────────────────────────────────────────────

def test_healthy_request_is_not_degraded(client, upstreams):
    # Control: no fault. Every later test is a diff against this one.
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] == []
    stops = assert_complete(body)
    assert all(written_by_model(s) for s in stops)
    assert body["trip_id"]
    assert upstreams.calls.count("nominatim") == 1 and upstreams.count("photon") == 0
    assert upstreams.count(MIRROR_A) == 1 and upstreams.count(MIRROR_B) == 0
    assert upstreams.llm.calls == ["model-a"]
    assert {s["name"] for s in stops}.isdisjoint({"Starbucks", "Ferme le Mercredi"})  # chain, closed that day
    assert any("photo_url" in s for s in stops)


# ── Geocoding ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("failure", ["http_503", "http_429", "unreachable", "timeout"])
def test_nominatim_down_photon_answers(client, upstreams, failure):
    # Nominatim fails; it is retried once, then Photon geocodes the request.
    upstreams.handlers["nominatim"] = {
        "http_503": upstreams.status(503), "http_429": upstreams.status(429),
        "unreachable": upstreams.unreachable(), "timeout": upstreams.hang(at_most=0.05),
    }[failure]
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["geocode:photon"]
    assert_complete(r.json())
    assert upstreams.count("nominatim") == 2 and upstreams.count("photon") == 1


def test_both_geocoders_down_is_503(client, upstreams):
    # No geocoder reachable: nothing can be built, so 503 with Retry-After.
    upstreams.handlers["nominatim"] = upstreams.status(503)
    upstreams.handlers["photon"] = upstreams.unreachable()
    r = post(client)
    assert r.status_code == 503
    assert r.headers["retry-after"] == "30"
    assert "Geocoding unavailable" in r.json()["detail"]
    assert upstreams.count(MIRROR_A) == 0  # nothing downstream was attempted


def test_unknown_place_is_400_without_retry_or_failover(client, upstreams):
    # Nominatim answers "no such place". That is an answer, not an outage.
    upstreams.handlers["nominatim"] = upstreams.json([])
    r = post(client, start_location="Zzyzx Qwerty")
    assert r.status_code == 400
    assert "Could not geocode" in r.json()["detail"]
    assert upstreams.count("nominatim") == 1  # not retried
    assert upstreams.count("photon") == 0     # no failover


def test_unknown_places_do_not_open_the_geocoder_breaker(client, upstreams):
    # Four typos in a row (the breaker threshold is 3) must not take Nominatim out of rotation.
    upstreams.handlers["nominatim"] = upstreams.json([])
    for i in range(4):
        assert post(client, start_location=f"Nowhere {i}").status_code == 400
    assert nominatim._nominatim_breaker.state == "closed"
    upstreams.handlers["nominatim"] = upstreams.json([{"lat": str(START[0]), "lon": str(START[1])}])
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == []
    assert upstreams.count("photon") == 0


# ── POI lookup ───────────────────────────────────────────────────────────────

def test_first_mirror_5xx_next_mirror_answers(client, upstreams):
    # Mirror A returns 504; the hedge moves to mirror B at once. Not a degraded answer.
    upstreams.handlers[MIRROR_A] = upstreams.status(504)
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == []
    assert_complete(r.json())
    assert [upstreams.count(m) for m in (MIRROR_A, MIRROR_B, MIRROR_C)] == [1, 1, 0]


def test_first_mirror_hangs_hedge_fires(client, upstreams, monkeypatch):
    # Mirror A accepts the request and never answers. After the hedge delay B is raced against it.
    monkeypatch.setattr(overpass, "HEDGE_AFTER_S", 0.05)
    upstreams.handlers[MIRROR_A] = upstreams.hang(at_most=5.0)
    started = time.monotonic()
    r = post(client)
    elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert r.json()["degraded"] == []
    assert_complete(r.json())
    assert upstreams.count(MIRROR_A) == 1 and upstreams.count(MIRROR_B) == 1
    assert elapsed < 3.0  # did not wait out the 5s hang


def test_mirror_returns_html_error_page_next_mirror_answers(client, upstreams):
    # Mirror A answers 200 with a load balancer's HTML page instead of JSON.
    upstreams.handlers[MIRROR_A] = upstreams.status(200, text="<html><body>Bad Gateway</body></html>")
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == []
    assert_complete(r.json())
    assert upstreams.count(MIRROR_B) == 1


def test_mirror_query_timeout_remark_next_mirror_answers(client, upstreams):
    # An overloaded Overpass server answers 200 with no elements and a "runtime error" remark.
    # That is a failed query, not an empty neighbourhood, so it must fail over rather than 404.
    upstreams.handlers[MIRROR_A] = upstreams.json(
        {"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 3 after 26 seconds."})
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == []
    assert_complete(r.json())
    assert upstreams.count(MIRROR_B) == 1


def test_failing_mirror_breaker_opens_and_mirror_is_skipped(client, upstreams):
    # Mirror A fails every time. Once its failures reach the breaker threshold it is no longer called.
    upstreams.handlers[MIRROR_A] = upstreams.status(503)
    threshold = overpass._breaker(MIRROR_A).failure_threshold
    for request_no in range(1, threshold + 3):
        overpass._memory_cache.clear()  # as if the fresh window had passed, so Overpass is asked again
        routes.itinerary_bucket.reset()  # this test is about the breaker, not the rate limit
        r = post(client)
        assert r.status_code == 200
        assert r.json()["degraded"] == []
        assert upstreams.count(MIRROR_A) == min(request_no, threshold)
    assert upstreams.count(MIRROR_B) == threshold + 2
    assert client.get("/api/health").json()["breakers"]["overpass:mirror-a.test"] == "open"


def test_all_mirrors_down_serves_stored_copy(client, upstreams):
    # A healthy request stores the POIs. Then every mirror goes down and the memory cache is gone
    # (a restart): the same request is answered from poi_cache and flagged.
    first = post(client).json()
    overpass._memory_cache.clear()
    upstreams.break_all_mirrors()
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["pois:stale"]
    stops = assert_complete(r.json())
    assert [s["id"] for s in stops] == [s["id"] for s in stops_of(first)]


def test_all_mirrors_down_nothing_stored_is_503(client, upstreams):
    # Every mirror down and no stored copy: one of the two ways a request may fail.
    upstreams.break_all_mirrors(upstreams.unreachable())
    r = post(client)
    assert r.status_code == 503
    assert r.headers["retry-after"] == "30"
    assert "Overpass" in r.json()["detail"]
    # every mirror was tried in every retry round, until its breaker opened
    tries = min(overpass.ROUNDS, overpass._breaker(MIRROR_A).failure_threshold)
    assert [upstreams.count(m) for m in (MIRROR_A, MIRROR_B, MIRROR_C)] == [tries] * 3
    assert upstreams.llm.calls == []


def test_overpass_finds_nothing_is_404(client, upstreams):
    # Overpass is up and there is nothing nearby. Not an outage, so 404 and no failover.
    upstreams.handlers[MIRROR_A] = upstreams.json({"elements": []})
    r = post(client)
    assert r.status_code == 404
    assert "No places found near Montmartre" in r.json()["detail"]
    assert upstreams.count(MIRROR_B) == 0
    with session_scope() as db:
        assert db.query(PoiCache).count() == 0  # an empty answer is not stored as a fallback


# ── Travel times ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("failure", ["http_500", "timeout", "bad_body"])
def test_ors_failing_falls_back_to_haversine(client, upstreams, monkeypatch, failure):
    # A key is configured and OpenRouteService fails: straight-line times are used and flagged.
    monkeypatch.setattr(config, "ORS_API_KEY", "test-key")
    upstreams.handlers["ors"] = {
        "http_500": upstreams.status(500), "timeout": upstreams.hang(at_most=0.05),
        "bad_body": upstreams.json({"error": "quota exceeded"}),
    }[failure]
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["matrix:haversine"]
    assert_complete(r.json())
    assert upstreams.count("ors") == 1


def test_ors_healthy_or_unconfigured_raises_no_flag(client, upstreams, monkeypatch):
    # Control for the test above. No key: ORS is never called and haversine is the design, not a fault.
    assert post(client).json()["degraded"] == []
    assert upstreams.count("ors") == 0
    # Key set and ORS answering: called once, no flag.
    monkeypatch.setattr(config, "ORS_API_KEY", "test-key")
    r = post(client)
    assert r.json()["degraded"] == []
    assert upstreams.count("ors") == 1


# ── LLM availability ─────────────────────────────────────────────────────────

def test_retired_model_fails_over_and_is_not_called_again(client, upstreams):
    # The primary model was retired (404). The next model writes the itinerary; nothing is degraded.
    upstreams.llm.behaviour["model-a"] = llm_error(404)
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == []
    assert all(written_by_model(s) for s in assert_complete(r.json()))
    assert upstreams.llm.calls == ["model-a", "model-b"]
    # One 404 is enough to open the breaker, so the second request goes straight to model-b.
    assert post(client).json()["degraded"] == []
    assert upstreams.llm.calls == ["model-a", "model-b", "model-b"]
    assert client.get("/api/health").json()["breakers"]["llm:model-a"] == "open"


@pytest.mark.parametrize("status", [429, 500, None])
def test_erroring_model_fails_over_and_breaker_opens_after_two(client, upstreams, status):
    # The primary model is rate limited, erroring or timing out. It gets two chances, then is skipped.
    upstreams.llm.behaviour["model-a"] = llm_error(status)
    for _ in range(3):
        r = post(client)
        assert r.status_code == 200
        assert r.json()["degraded"] == []
    assert upstreams.llm.calls == ["model-a", "model-b", "model-a", "model-b", "model-b"]


def test_every_model_failing_writes_text_from_tags(client, upstreams):
    # No model answers. The itinerary is written deterministically and flagged.
    upstreams.llm.behaviour = {"model-a": llm_error(429), "model-b": llm_error(None)}
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] == ["llm:fallback"]
    stops = assert_complete(body)
    assert not any(written_by_model(s) for s in stops)
    assert body["overview"].startswith("A 10-hour route through Paris")
    assert all(s["tip"] for s in stops)
    assert upstreams.llm.calls == ["model-a", "model-b"]


def test_no_api_key_writes_text_from_tags(client, upstreams, monkeypatch):
    # GROQ_API_KEY is unset (CI, or a fresh deploy). The real client factory refuses to build a
    # client, and the request is answered the same way as when every model is down.
    monkeypatch.setattr(llm, "get_client", upstreams.real_get_client)
    assert config.GROQ_API_KEY == ""
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["llm:fallback"]
    assert_complete(r.json())


@pytest.mark.parametrize("completion", ["Sure! Here is your itinerary: {", "", '["a", "list"]'])
def test_model_returns_invalid_json(client, upstreams, completion):
    # The model's completion cannot be parsed as a JSON object. Text is written from tags instead.
    upstreams.llm.behaviour["model-a"] = lambda prompt: completion
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["llm:fallback"]
    assert not any(written_by_model(s) for s in assert_complete(r.json()))


# ── LLM contract violations (the answer parses but breaks the contract) ──────

def broken(mutate):
    """A model that answers correctly and then has its answer broken by `mutate`."""
    def answer(prompt):
        raw = model_answer(prompt)
        mutate(raw)
        return raw
    return answer


def test_model_omits_stops(client, upstreams):
    # The model describes only the first two stops. The rest get deterministic text; none are dropped.
    upstreams.llm.behaviour["model-a"] = broken(lambda raw: raw.update(stops=raw["stops"][:2]))
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["llm:repaired"]
    stops = assert_complete(r.json())
    assert [written_by_model(s) for s in stops] == [True, True, False, False, False, False]


def test_model_duplicates_a_stop(client, upstreams):
    # Stop 1 is described twice with different text. The first description wins, nothing is doubled.
    def duplicate(raw):
        raw["stops"].append({"id": 1, "description": "Model prose, second attempt.", "tip": "x"})
    upstreams.llm.behaviour["model-a"] = broken(duplicate)
    r = post(client)
    assert r.json()["degraded"] == ["llm:repaired"]
    stops = assert_complete(r.json())
    assert len({s["id"] for s in stops}) == N_STOPS
    assert stops[0]["description"] == "Model prose for stop 1."


def test_model_invents_a_stop(client, upstreams):
    # The model adds stop 99 with made-up coordinates. It never reaches the response.
    def invent(raw):
        raw["stops"].append({"id": 99, "name": "Tour Eiffel", "lat": 48.8584, "lon": 2.2945,
                             "description": "Model prose about a place that is not in the route."})
    upstreams.llm.behaviour["model-a"] = broken(invent)
    r = post(client)
    assert r.json()["degraded"] == ["llm:repaired"]
    stops = assert_complete(r.json())
    assert "Tour Eiffel" not in json.dumps(r.json())
    assert all(written_by_model(s) for s in stops)


def test_model_returns_blank_descriptions(client, upstreams):
    # Every stop is present but two descriptions are empty or whitespace.
    def blank(raw):
        raw["stops"][0]["description"] = ""
        raw["stops"][1]["description"] = "   "
    upstreams.llm.behaviour["model-a"] = broken(blank)
    r = post(client)
    assert r.json()["degraded"] == ["llm:repaired"]
    assert_complete(r.json())  # asserts every description is non-empty


def test_model_excludes_stops_the_traveller_did_not_rule_out(client, upstreams):
    # No notes were given, so the model has no grounds to exclude anything. The exclusion is ignored.
    upstreams.llm.behaviour["model-a"] = broken(lambda raw: raw.update(excluded=[2, 3]))
    r = post(client)
    assert r.json()["degraded"] == ["llm:repaired"]
    assert_complete(r.json())


def test_model_excludes_most_of_the_route(client, upstreams):
    # Notes were given, but excluding more than half the route is treated as the model misbehaving.
    upstreams.llm.behaviour["model-a"] = broken(lambda raw: raw.update(excluded=[1, 2, 3, 4]))
    r = post(client, notes="no museums please")
    assert r.json()["degraded"] == ["llm:repaired"]
    assert_complete(r.json())


def test_exclusion_backed_by_notes_is_honoured(client, upstreams):
    # Control: with notes, excluding one stop (and not describing it) is legal and not degraded.
    def exclude_one(raw):
        raw["excluded"] = [2]
        raw["stops"] = [s for s in raw["stops"] if s["id"] != 2]
    upstreams.llm.behaviour["model-a"] = broken(exclude_one)
    r = post(client, notes="no museums please")
    assert r.status_code == 200
    assert r.json()["degraded"] == []
    assert_complete(r.json(), n_stops=N_STOPS - 1)


@pytest.mark.parametrize("field,value,flags", [
    ("stops", "see above", ["llm:repaired"]),
    ("stops", None, ["llm:repaired"]),
    ("excluded", 2, ["llm:repaired"]),
    # "days" only carries the optional theme and summary, so a bad one is replaced without a flag
    ("days", 1, []),
    ("days", [{"day": [1]}], []),
])
def test_model_returns_wrong_types(client, upstreams, field, value, flags):
    # Valid JSON, wrong shape: a field that should be a list is a string, a number or null.
    # The two "days" cases and the "excluded" case used to be a TypeError and a 500.
    upstreams.llm.behaviour["model-a"] = broken(lambda raw: raw.update({field: value}))
    r = post(client, notes="no museums please")
    assert r.status_code == 200
    assert r.json()["degraded"] == flags
    assert_complete(r.json())
    assert all(day["theme"] and day["summary"] for day in r.json()["days"])


# ── Photos ───────────────────────────────────────────────────────────────────

def test_photo_lookup_hanging_does_not_hold_the_response(client, upstreams, monkeypatch):
    # Wikipedia and Wikidata accept connections and never answer. The response leaves without photos
    # after PHOTO_WAIT_S, not after the lookups' own timeouts.
    monkeypatch.setattr(pipeline, "PHOTO_WAIT_S", 0.2)
    upstreams.handlers["wikipedia"] = upstreams.hang(at_most=4.0)
    upstreams.handlers["wikidata"] = upstreams.hang(at_most=4.0)
    started = time.monotonic()
    r = post(client)
    elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert r.json()["degraded"] == ["photos:unavailable"]
    stops = assert_complete(r.json())
    assert not any("photo_url" in s for s in stops)
    assert elapsed < 3.0  # the hang lasts 4s


def test_photo_services_erroring_is_flagged(client, upstreams):
    # Wikipedia and Wikidata answer 503. Stops have no photos and the response says why.
    upstreams.handlers["wikipedia"] = upstreams.status(503)
    upstreams.handlers["wikidata"] = upstreams.unreachable()
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["photos:unavailable"]
    assert not any("photo_url" in s for s in assert_complete(r.json()))


def test_stop_without_a_wikipedia_page_is_not_a_fault(client, upstreams):
    # Control: 404 from Wikipedia means "no article", which is normal and raises no flag.
    upstreams.handlers["wikipedia"] = upstreams.status(404)
    r = post(client)
    assert r.json()["degraded"] == []


# ── Local models and the solver ──────────────────────────────────────────────

def test_faiss_index_missing(client, upstreams, monkeypatch, tmp_path):
    # data/faiss.index does not exist (fresh container, failed build). The prompt gets no context.
    from backend.ml.rag import retriever, vector_store
    monkeypatch.setattr(retriever, "_index", None)
    monkeypatch.setattr(vector_store, "INDEX_PATH", tmp_path / "faiss.index")
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["rag:unavailable"]
    assert all(written_by_model(s) for s in assert_complete(r.json()))
    assert "Rue Lepic" not in upstreams.llm.prompts[0]


def test_ranker_model_missing(client, upstreams, monkeypatch, tmp_path):
    # data/ranker_model.pkl does not exist. POIs are ordered by embedding similarity instead.
    from backend.ml.ranker import model, scorer
    monkeypatch.setattr(scorer, "_ranker", None)
    monkeypatch.setattr(model, "MODEL_PATH", tmp_path / "ranker_model.pkl")
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] == ["ranker:semantic"]
    assert_complete(body)
    assert body["ranking_explanations"] == {} and "global_feature_importance" not in body


@pytest.mark.parametrize("failure", ["returns_none", "raises"])
def test_route_solver_fails(client, upstreams, monkeypatch, failure):
    # OR-Tools finds no solution, or crashes. The greedy ordering is used and every stop keeps a time.
    def solve_day(*args, **kwargs):
        if failure == "raises":
            raise RuntimeError("solver crashed")
        return None
    monkeypatch.setattr(scheduler, "solve_day", solve_day)
    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == ["route:greedy"]
    stops = assert_complete(r.json())
    times = [s["arrival_time"] for s in stops]
    assert times == sorted(times)


# ── Storage ──────────────────────────────────────────────────────────────────

def test_database_down_itinerary_is_still_returned(client, upstreams):
    # The database refuses every statement: the POI cache write and the trip save both fail.
    # The traveller still gets the itinerary; it just has no id to come back to.
    with database_down():
        r = post(client)
        health = client.get("/api/health").json()
    assert r.status_code == 200
    body = r.json()
    assert body["trip_id"] is None
    assert body["degraded"] == []
    assert_complete(body)
    assert health["status"] == "degraded" and health["database"].startswith("error")


def test_trip_save_failing_itinerary_is_still_returned(client, upstreams, monkeypatch):
    # Only the trip insert fails (for example a constraint or a full disk).
    def save_trip(*args, **kwargs):
        raise OperationalError("INSERT INTO trips", {}, OSError("disk full"))
    monkeypatch.setattr(routes, "save_trip", save_trip)
    r = post(client)
    assert r.status_code == 200
    assert r.json()["trip_id"] is None
    assert_complete(r.json())
    with session_scope() as db:
        assert db.query(Trip).count() == 0


# ── Deadline ─────────────────────────────────────────────────────────────────

def test_deadline_spent_before_pois_is_503(client, upstreams, monkeypatch):
    # Geocoding uses up the whole request budget. Overpass is not even attempted.
    monkeypatch.setattr(config, "REQUEST_DEADLINE_S", 0.15)
    upstreams.handlers["nominatim"] = upstreams.slow(0.3, upstreams.handlers["nominatim"])
    r = post(client)
    assert r.status_code == 503
    assert "deadline" in r.json()["detail"]
    assert upstreams.count(MIRROR_A) == 0


def test_deadline_spent_mid_pipeline_degrades_instead_of_failing(client, upstreams, monkeypatch):
    # OpenRouteService hangs until the request's budget is gone, after the POIs are in hand. The
    # stages that still need the network are skipped: no LLM call is made, text is written from tags.
    monkeypatch.setattr(config, "REQUEST_DEADLINE_S", 1.5)
    monkeypatch.setattr(config, "ORS_API_KEY", "test-key")
    upstreams.handlers["ors"] = upstreams.hang(at_most=5.0)
    for _ in range(2):
        started = time.monotonic()
        r = post(client)
        assert r.status_code == 200
        assert time.monotonic() - started < 3.0  # the hang was cut off at the deadline, not at 5s
        # photos get a 0.1s grace period at this point, which the fake may or may not make
        flags = [f for f in r.json()["degraded"] if f != "photos:unavailable"]
        assert flags == ["matrix:haversine", "rag:unavailable", "llm:fallback"]
        assert_complete(r.json())
    assert upstreams.llm.calls == []
    # Running out of our own time says nothing about the models' health: their breakers stay closed
    # and the next request, with a normal budget, is written by the model.
    assert all(b.state == "closed" for b in llm._breakers.values())
    monkeypatch.setattr(config, "REQUEST_DEADLINE_S", 60.0)
    monkeypatch.setattr(config, "ORS_API_KEY", "")
    r = post(client)
    assert r.json()["degraded"] == []
    assert upstreams.llm.calls == ["model-a"]


def test_slow_overpass_answer_is_not_waited_for_past_the_deadline(client, upstreams, monkeypatch):
    # Every mirror would answer, but only after the request's budget. The request fails at the
    # deadline (503) instead of running on for as long as the mirrors take.
    monkeypatch.setattr(config, "REQUEST_DEADLINE_S", 0.4)
    upstreams.break_all_mirrors(upstreams.hang(at_most=5.0))
    started = time.monotonic()
    r = post(client)
    assert r.status_code == 503
    assert time.monotonic() - started < 2.0


# ── Several faults at once ───────────────────────────────────────────────────

def test_eight_faults_at_once_still_returns_a_complete_itinerary(client, upstreams, monkeypatch, tmp_path):
    # Nominatim down, every Overpass mirror down (stored copy exists), no ranker model, ORS failing,
    # solver failing, no FAISS index, every LLM model failing, photo services hanging.
    from backend.ml.rag import retriever, vector_store
    from backend.ml.ranker import model, scorer
    store_stale_pois()
    upstreams.handlers["nominatim"] = upstreams.unreachable()
    upstreams.break_all_mirrors()
    monkeypatch.setattr(scorer, "_ranker", None)
    monkeypatch.setattr(model, "MODEL_PATH", tmp_path / "ranker_model.pkl")
    monkeypatch.setattr(config, "ORS_API_KEY", "test-key")
    upstreams.handlers["ors"] = upstreams.status(502)
    monkeypatch.setattr(scheduler, "solve_day", lambda *a, **k: None)
    monkeypatch.setattr(retriever, "_index", None)
    monkeypatch.setattr(vector_store, "INDEX_PATH", tmp_path / "faiss.index")
    upstreams.llm.behaviour = {"model-a": llm_error(404), "model-b": llm_error(500)}
    monkeypatch.setattr(pipeline, "PHOTO_WAIT_S", 0.2)
    upstreams.handlers["wikipedia"] = upstreams.hang(at_most=4.0)
    upstreams.handlers["wikidata"] = upstreams.hang(at_most=4.0)

    r = post(client)
    assert r.status_code == 200
    assert r.json()["degraded"] == [
        "geocode:photon", "pois:stale", "ranker:semantic", "matrix:haversine",
        "route:greedy", "rag:unavailable", "llm:fallback", "photos:unavailable",
    ]
    assert_complete(r.json())
    assert r.json()["trip_id"]
    with session_scope() as db:
        assert db.get(Trip, r.json()["trip_id"]).degraded == r.json()["degraded"]


def test_mirrors_down_and_database_down_is_503(client, upstreams):
    # The stale copy lives in the database. With both gone there is no POI data at all.
    store_stale_pois()
    upstreams.break_all_mirrors()
    with database_down():
        r = post(client)
    assert r.status_code == 503
    assert r.headers["retry-after"] == "30"


# ── Streaming endpoint ───────────────────────────────────────────────────────

def sse_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in re.split(r"\r?\n\r?\n", text.strip()):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


STEPS = ["geocoding", "fetching_pois", "ranking", "optimizing", "retrieving", "generating"]


def test_stream_sends_progress_in_order_then_result(client, upstreams):
    # Control for the stream: six progress events in pipeline order, then one result.
    r = client.post("/api/itinerary/stream", json=trip_request())
    assert r.status_code == 200
    events = sse_events(r.text)
    assert [name for name, _ in events] == ["progress"] * 6 + ["result"]
    assert [data["step"] for _, data in events[:6]] == STEPS
    percents = [data["progress"] for _, data in events[:6]]
    assert percents == sorted(percents)
    result = events[-1][1]
    assert result["degraded"] == []
    assert_complete(result)


def test_stream_result_carries_degraded_flags(client, upstreams):
    # Same faults as on the plain endpoint, same flags in the result event.
    upstreams.handlers["nominatim"] = upstreams.status(503)
    upstreams.llm.behaviour = {"model-a": llm_error(429), "model-b": llm_error(429)}
    events = sse_events(client.post("/api/itinerary/stream", json=trip_request()).text)
    assert events[-1][0] == "result"
    assert events[-1][1]["degraded"] == ["geocode:photon", "llm:fallback"]
    assert_complete(events[-1][1])


def test_stream_sends_error_event_when_the_pipeline_fails(client, upstreams):
    # Every mirror down, nothing stored: progress stops at the failing stage and one error follows.
    upstreams.break_all_mirrors()
    r = client.post("/api/itinerary/stream", json=trip_request())
    assert r.status_code == 200  # the stream itself opened; the failure is an event
    events = sse_events(r.text)
    assert [data["step"] for name, data in events if name == "progress"] == ["geocoding", "fetching_pois"]
    assert events[-1][0] == "error"
    assert "Overpass" in events[-1][1]["message"]
    assert not any(name == "result" for name, _ in events)


def test_stream_error_event_for_unknown_place(client, upstreams):
    # The 400 case on the stream: an error event with the geocoder's message.
    upstreams.handlers["nominatim"] = upstreams.json([])
    events = sse_events(client.post("/api/itinerary/stream", json=trip_request()).text)
    assert [name for name, _ in events] == ["progress", "error"]
    assert "Could not geocode" in events[-1][1]["message"]


def test_stream_client_disconnect_stops_the_pipeline_and_saves_nothing(upstreams):
    # The client hangs up while the pipeline is geocoding. The pipeline stops at the next stage
    # boundary: Overpass and the LLM are never called and no trip is stored for nobody to read.
    from backend.tests.conftest import raw_asgi
    upstreams.handlers["nominatim"] = upstreams.slow(0.4, upstreams.handlers["nominatim"])
    status, body = raw_asgi("POST", "/api/itinerary/stream", trip_request(), disconnect_on=b"geocoding")
    assert status == 200 and b"result" not in body
    deadline = time.monotonic() + 5
    while upstreams.count("nominatim") == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.8)  # the worker thread finishes geocoding and reaches the next progress call
    assert upstreams.count("nominatim") == 1
    assert upstreams.count(MIRROR_A) == 0
    assert upstreams.llm.calls == []
    with session_scope() as db:
        assert db.query(Trip).count() == 0


# ── LLM breaker bookkeeping (llm.chat called directly) ───────────────────────

def ask(deadline_s: float | None = None):
    from backend.core.resilience import Deadline
    return llm.chat([{"role": "user", "content": "[1] 09:00 Somewhere"}],
                    deadline=Deadline(deadline_s) if deadline_s is not None else None)


def test_timeout_under_a_shortened_deadline_is_not_held_against_the_model(upstreams):
    # 5 seconds of the request are left, so the call gets a 5s timeout instead of 40s and times
    # out. That measures our budget, not the model: after two such requests it is still in rotation.
    upstreams.llm.behaviour = {"model-a": llm_error(None), "model-b": llm_error(None)}
    for _ in range(2):
        with pytest.raises(llm.LLMUnavailable):
            ask(deadline_s=5)
    assert upstreams.llm.calls == ["model-a", "model-b"] * 2
    assert all(b.state == "closed" and b._failures == 0 for b in llm._breakers.values())
    # the same timeout with the full budget does count
    with pytest.raises(llm.LLMUnavailable):
        ask(deadline_s=120)
    assert llm._breakers["model-a"]._failures == 1


def test_no_completion_is_started_without_a_minimum_budget(upstreams):
    # One second left: a completion cannot finish, so none is attempted and none is billed.
    with pytest.raises(llm.LLMUnavailable, match="deadline"):
        ask(deadline_s=1)
    assert upstreams.llm.calls == []
    assert llm._breakers == {}


def test_unexpected_error_during_half_open_probe_does_not_wedge_the_breaker(upstreams):
    # model-a's breaker is open, its reset window passes, and the single probe call dies with an
    # error the client never anticipated. The breaker used to keep the probe slot forever, so a
    # recovered model was never called again; and the error escaped as a 500.
    upstreams.llm.behaviour["model-a"] = llm_error(500)
    ask(), ask()
    breaker = llm._breakers["model-a"]
    assert breaker.state == "open"
    breaker._opened_at -= 1000  # the reset window has passed

    def malformed(prompt):
        raise AttributeError("'NoneType' object has no attribute 'prompt_tokens'")
    upstreams.llm.behaviour["model-a"] = malformed
    _, meta = ask()  # the probe fails, model-b answers; nothing escapes
    assert meta["model"] == "model-b" and meta["failed_over"] == ["model-a: AttributeError"]
    assert breaker.state == "open" and breaker._probing is False

    breaker._opened_at -= 100000  # later, the model has recovered
    del upstreams.llm.behaviour["model-a"]
    assert ask()[1]["model"] == "model-a"
    assert breaker.state == "closed"


def test_request_specific_4xx_moves_on_without_counting_against_the_model(upstreams):
    # A 400 (prompt over the context length, malformed tool call) is about this request. The next
    # model gets it, and however many times it happens the first model's breaker stays closed.
    upstreams.llm.behaviour["model-a"] = llm_error(400)
    for _ in range(4):
        assert ask()[1]["model"] == "model-b"
    assert upstreams.llm.calls == ["model-a", "model-b"] * 4
    assert llm._breakers["model-a"].state == "closed" and llm._breakers["model-a"]._failures == 0


def test_breaker_call_settles_the_probe_whatever_is_raised():
    # CircuitBreaker.call gives the same guarantee for anything, including BaseException.
    from backend.core.resilience import CircuitBreaker

    class Killed(BaseException):
        pass

    def die():
        raise Killed()
    now = [0.0]
    breaker = CircuitBreaker("x", failure_threshold=1, reset_after=30, clock=lambda: now[0])
    breaker.record_failure()
    now[0] = 31
    with pytest.raises(Killed):
        breaker.call(die)
    assert breaker._probing is False and breaker.state == "open"
    now[0] = 62
    assert breaker.call(lambda: "ok") == "ok" and breaker.state == "closed"


def test_breaker_abandon_returns_the_probe_slot_without_a_verdict():
    from backend.core.resilience import CircuitBreaker
    now = [0.0]
    breaker = CircuitBreaker("x", failure_threshold=1, reset_after=30, clock=lambda: now[0])
    breaker.record_failure()
    now[0] = 31
    assert breaker.allow() is True and breaker.allow() is False  # one probe at a time
    breaker.abandon()
    assert breaker.state == "half-open"  # neither closed nor re-opened
    assert breaker.allow() is True       # the next caller may probe
