"""The pipeline's own logic: request validation, sizing helpers, and one healthy run end to end."""

from datetime import date

import pytest

from backend import pipeline
from backend.core.resilience import Deadline
from backend.db.models import Trip
from backend.db.session import session_scope
from backend.pipeline import TripRequest, attach_stop_extras, effective_goals, n_days, run_pipeline, top_k
from backend.tests.conftest import MIRROR_A, N_STOPS, trip_request


# ── Request validation (422 before anything runs) ────────────────────────────

@pytest.mark.parametrize("override", [
    {"goals": ["food", "skydiving"]},          # unknown goal
    {"goals": []},                             # no goal at all
    {"goals": ["food"] * 9},                   # more than 8 entries
    {"duration_hours": 0},
    {"duration_hours": 73},
    {"duration_hours": "all day"},
    {"trip_date": "next tuesday"},
    {"trip_date": "2026-13-40"},
    {"transport": "teleport"},
    {"pace": "frantic"},
    {"dietary": "carnivore"},
    {"start_time": "midnight"},
    {"city": ""},
    {"start_location": "x" * 161},
    {"notes": "x" * 501},
])
def test_invalid_request_is_422_and_touches_no_upstream(client, upstreams, override):
    r = client.post("/api/itinerary", json=trip_request(**override))
    assert r.status_code == 422
    assert list(override)[0] in str(r.json()["detail"])  # the error names the bad field
    assert upstreams.calls == [] and upstreams.llm.calls == []


def test_missing_required_fields_are_422(client, upstreams):
    assert client.post("/api/itinerary", json={"city": "Paris"}).status_code == 422
    assert client.post("/api/itinerary/stream", json={"goals": ["food"]}).status_code == 422


def test_request_defaults_and_duplicate_goals():
    req = TripRequest(city="Paris", start_location="Montmartre", goals=["food", "culture", "food"])
    assert req.goals == ["food", "culture"]  # order kept, duplicates dropped
    assert (req.duration_hours, req.transport, req.pace, req.start_time) == (6, "walking", "moderate", "morning")
    assert req.trip_date == "" and req.notes == ""


# ── Sizing helpers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("hours,days", [(1, 1), (10, 1), (11, 2), (20, 2), (21, 3), (72, 8)])
def test_n_days_is_one_per_ten_hours(hours, days):
    assert n_days(hours) == days


def test_top_k_scales_with_pace_and_never_starves_a_goal():
    assert top_k(6, "relaxed", 2) == 4
    assert top_k(6, "moderate", 2) == 6
    assert top_k(6, "packed", 2) == 9
    assert top_k(24, "moderate", 2) == 18    # three days
    assert top_k(6, "relaxed", 5) == 10      # five goals need at least two candidates each


def test_trip_weekday():
    assert pipeline.trip_weekday("2026-06-03") == 2  # a Wednesday
    assert pipeline.trip_weekday("") == date.today().weekday()


def test_returning_visitors_get_hidden_gems_added_once():
    assert effective_goals(["food"], "first_time") == ["food"]
    assert effective_goals(["food"], "returning") == ["food", "hidden_gems"]
    assert effective_goals(["hidden_gems", "food"], "returning") == ["hidden_gems", "food"]


def test_attach_stop_extras_matches_by_id():
    route = [{"day": 1, "pois": [
        {"id": 1, "name": "Museum", "tags": {"opening_hours": "Mo-Su 10:00-18:00"}},
        {"id": 2, "name": "Cafe", "tags": {}},
    ]}]
    itinerary = {"days": [{"stops": [{"id": 1, "name": "Museum"}, {"id": 2, "name": "Cafe"}, {"id": 3}]}]}
    attach_stop_extras(itinerary, route, {"Museum": "https://img.test/m.jpg"})
    first, second, unknown = itinerary["days"][0]["stops"]
    assert first["photo_url"] == "https://img.test/m.jpg" and first["opening_hours"] == "Mo-Su 10:00-18:00"
    assert "photo_url" not in second and "opening_hours" not in second
    assert unknown == {"id": 3}  # a stop that is not in the route is left alone


# ── One healthy run, called directly ─────────────────────────────────────────

def test_run_pipeline_reports_progress_timings_and_llm_usage(upstreams):
    progress = []
    out = run_pipeline(TripRequest(**trip_request()), on_progress=lambda step, msg, pct: progress.append((step, pct)))
    assert progress == [("geocoding", 10), ("fetching_pois", 25), ("ranking", 45), ("optimizing", 60),
                        ("retrieving", 75), ("generating", 90)]
    assert out.degraded == [] and out.result["degraded"] == []
    assert sum(len(d["stops"]) for d in out.result["days"]) == N_STOPS
    # every stage reported a duration, including the two that run on worker threads
    assert {"pipeline", "geocode", "fetch_pois", "overpass.query", "rank", "route", "route.solve",
            "retrieve", "llm", "llm.call", "photos"} <= set(out.timings_ms)
    assert out.timings_ms["pipeline"] >= out.timings_ms["llm"]
    assert out.result["timings_ms"] == out.timings_ms
    assert out.llm_meta["model"] == "model-a" and out.llm_meta["failed_over"] == []
    assert out.llm_meta["prompt_tokens"] > 0 and out.llm_meta["completion_tokens"] > 0


def test_prompt_carries_retrieved_context_and_the_route_but_no_coordinates(upstreams):
    out = run_pipeline(TripRequest(**trip_request()))
    prompt = upstreams.llm.prompts[0]
    # chunks about the requested city are listed before chunks about other cities
    assert "[Source 1 - Paris]" in prompt and "[Source 2 - Paris]" in prompt
    assert prompt.index("[Source 2 - Paris]") < prompt.index("Tokyo")
    for day in out.route:
        for p in day["pois"]:
            assert p["name"] in prompt
            assert str(p["lat"]) not in prompt and str(p["lon"]) not in prompt


def test_explanations_are_in_the_response_but_not_in_the_stored_route(upstreams):
    out = run_pipeline(TripRequest(**trip_request()))
    assert set(out.result["ranking_explanations"]) == {p["name"] for d in out.route for p in d["pois"]}
    assert all("explanation" not in p for d in out.route for p in d["pois"])
    assert out.result["global_feature_importance"]
    quiet = run_pipeline(TripRequest(**trip_request()), explain=False)
    assert "ranking_explanations" not in quiet.result


def test_stops_carry_photos_and_opening_hours_from_tags(upstreams):
    out = run_pipeline(TripRequest(**trip_request()))
    stops = {s["name"]: s for d in out.result["days"] for s in d["stops"]}
    assert stops["Espace Dali"]["photo_url"] == "https://upload.test/thumb/500px-photo.jpg"  # resized to 500px
    assert stops["Theatre de l'Atelier"]["photo_url"].startswith("https://commons.wikimedia.org/wiki/Special:FilePath/")
    assert stops["Le Petit Moulin"]["opening_hours"] == "Mo-Su 08:00-23:00"
    assert "photo_url" not in stops["Le Petit Moulin"]  # no wikipedia or wikidata tag


def test_request_options_shape_the_overpass_query(upstreams):
    # mobility sets the radius, and a returning visitor adds the hidden_gems category.
    run_pipeline(TripRequest(**trip_request(mobility="active", familiarity="returning")))
    query = next(kwargs["data"]["data"] for service, kwargs in upstreams.requests if service == MIRROR_A)
    assert "(around:3000," in query
    assert query.count("out center") == 3
    assert 'tourism~"attraction|viewpoint"' in query


def test_dietary_filter_reaches_the_itinerary(upstreams):
    out = run_pipeline(TripRequest(**trip_request(dietary="vegan")))
    food = [s["name"] for d in out.result["days"] for s in d["stops"] if s["category"] == "food"]
    assert food == ["Bistro Lepic"]  # the only stop tagged diet:vegan


def test_start_time_sets_the_first_arrival(upstreams):
    out = run_pipeline(TripRequest(**trip_request(start_time="afternoon")))
    first = out.result["days"][0]["stops"][0]["arrival_time"]
    assert "13:00" <= first < "14:00"


def test_every_stage_gets_the_same_deadline(upstreams, monkeypatch):
    # One budget for the whole request: the timeout handed to Overpass is capped by what is left.
    monkeypatch.setattr(pipeline.config, "REQUEST_DEADLINE_S", 5.0)
    run_pipeline(TripRequest(**trip_request()))
    timeouts = {service: kwargs["timeout"] for service, kwargs in upstreams.requests if "timeout" in kwargs}
    assert timeouts["nominatim"] <= 5.0 and timeouts[MIRROR_A] <= 5.0
    assert Deadline(5.0).timeout(25.0) <= 5.0


# ── Through HTTP: what is stored and what the metrics endpoints show ─────────

def test_trip_is_stored_with_request_route_and_degraded_flags(client, upstreams):
    upstreams.handlers["nominatim"] = upstreams.status(503)
    body = client.post("/api/itinerary", json=trip_request(notes="window seats")).json()
    with session_scope() as db:
        trip = db.get(Trip, body["trip_id"])
        assert trip.city == "Paris" and trip.user_id is None
        assert trip.request["notes"] == "window seats"
        assert trip.degraded == ["geocode:photon"]
        assert [p["id"] for d in trip.route for p in d["pois"]] == [s["id"] for d in body["days"] for s in d["stops"]]
        assert "timings_ms" not in trip.itinerary and "ranking_explanations" not in trip.itinerary


def test_latency_endpoint_reports_percentiles_per_stage(client, upstreams):
    client.post("/api/itinerary", json=trip_request())
    stats = client.get("/api/metrics/latency").json()
    for name in ("pipeline", "geocode", "fetch_pois", "rank", "route", "llm"):
        assert stats[name]["count"] >= 1
        assert 0 <= stats[name]["p50_ms"] <= stats[name]["p95_ms"] <= stats[name]["max_ms"]


def test_health_reports_database_and_breakers(client, upstreams):
    client.post("/api/itinerary", json=trip_request())
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["database"] == "ok"
    assert health["breakers"]["nominatim"] == "closed"
    assert health["breakers"]["overpass:mirror-a.test"] == "closed"
    assert health["breakers"]["llm:model-a"] == "closed"


@pytest.mark.parametrize("hours", [3, 6])
def test_short_trip_ends_within_its_duration(client, upstreams, hours):
    # No fault. Six stops are selected, but only those that fit between 09:00 and 09:00 + hours
    # are routed: the last visit must be over by then, and what is routed is still complete.
    r = client.post("/api/itinerary", json=trip_request(duration_hours=hours))
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] == []
    stops = [s for d in body["days"] for s in d["stops"]]
    assert 1 <= len(stops) < N_STOPS
    assert all(s["description"].strip() for s in stops)

    def minutes(hhmm: str) -> int:
        return int(hhmm[:2]) * 60 + int(hhmm[3:])
    assert minutes(stops[0]["arrival_time"]) >= 9 * 60
    assert minutes(stops[-1]["arrival_time"]) + stops[-1]["duration_min"] <= (9 + hours) * 60
    # the stored route was trimmed too, so the trip can be reopened exactly as it was shown
    with session_scope() as db:
        route = db.get(Trip, body["trip_id"]).route
    assert [p["id"] for d in route for p in d["pois"]] == [s["id"] for s in stops]
