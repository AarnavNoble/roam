"""
The itinerary pipeline: request in, itinerary out.

    geocode ─┬─> fetch POIs ─> rank ─> route ─┬─> write (LLM) ─┬─> assemble
    retrieve ┘ (parallel with geocode)        └─> photos ──────┘ (parallel with LLM)

Every stage runs in a span, under one request deadline. A stage that depends
on an outside service has a fallback, and using one is recorded in `degraded`
so the client (and the tests) can tell a full answer from a reduced one:

    geocode:photon        Nominatim was down, Photon answered
    pois:stale            every Overpass mirror was down, served the stored copy
    matrix:haversine      OpenRouteService failed, used straight-line travel times
    route:greedy          the solver returned nothing, used the greedy ordering
    ranker:semantic       no trained model could be loaded, ranked by similarity
    rag:unavailable       no retrieval context
    llm:fallback          no model answered, text was written from tags
    llm:repaired          the model's answer broke the contract and was repaired
    photos:unavailable    photo lookup failed or ran out of time

Only two things fail a request: a place that does not exist (400) and having
no POI data at all, live or stored (503).
"""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime
from typing import Callable, Literal

from pydantic import BaseModel, Field, field_validator

from backend import config
from backend.core.resilience import Deadline, UpstreamError
from backend.core.telemetry import collect_timings, in_context, stage
from backend.ml.optimizer.scheduler import build_itinerary_with_meta
from backend.ml.rag.retriever import format_context, retrieve
from backend.ml.ranker.scorer import get_ranker, rank_pois
from backend.services import llm, writer
from backend.services.nominatim import geocode_with_source
from backend.services.overpass import fetch_pois_with_source
from backend.services.wiki_photos import fetch_photos_for_itinerary

MOBILITY_RADIUS = {"easy": 1500, "moderate": 2000, "active": 3000}
PACE_STOPS_PER_DAY = {"relaxed": 4, "moderate": 6, "packed": 9}
START_HOUR = {"morning": 9, "afternoon": 13, "evening": 17}
PHOTO_WAIT_S = 6.0

Goal = Literal["food", "nature", "history", "culture", "nightlife", "shopping", "adventure", "hidden_gems"]


class TripRequest(BaseModel):
    city: str = Field(min_length=1, max_length=120)            # area for context (e.g. "Paris")
    start_location: str = Field(min_length=1, max_length=160)  # where you are now (e.g. "Montmartre")
    duration_hours: int = Field(default=6, ge=1, le=72)
    goals: list[Goal] = Field(min_length=1, max_length=8)
    transport: Literal["walking", "transit", "cycling", "driving"] = "walking"
    pace: Literal["relaxed", "moderate", "packed"] = "moderate"
    budget: Literal["free", "budget", "mid", "splurge"] = "mid"
    style: Literal["solo", "couple", "family", "group"] = "solo"
    dietary: Literal["none", "vegetarian", "vegan", "halal", "kosher"] = "none"
    mobility: Literal["easy", "moderate", "active"] = "moderate"
    familiarity: Literal["first_time", "returning"] = "first_time"
    start_time: Literal["morning", "afternoon", "evening"] = "morning"
    notes: str = Field(default="", max_length=500)
    trip_date: str = ""                                        # ISO date, defaults to today

    @field_validator("goals")
    @classmethod
    def _unique_goals(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @field_validator("trip_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        if v:
            _datetime.fromisoformat(v)  # raises ValueError -> 422
        return v


class NoPlacesFound(Exception):
    """Overpass answered and there is nothing matching nearby."""


@dataclass
class PipelineOutput:
    result: dict                      # what the client renders
    route: list[dict]                 # structured route the text was written from
    degraded: list[str] = field(default_factory=list)
    timings_ms: dict = field(default_factory=dict)
    llm_meta: dict = field(default_factory=dict)


def n_days(duration_hours: int) -> int:
    return max(1, math.ceil(duration_hours / 10))


def top_k(duration_hours: int, pace: str, n_goals: int) -> int:
    """Stops to select: scales with pace, always enough for each goal to appear."""
    return max(n_days(duration_hours) * PACE_STOPS_PER_DAY.get(pace, 6), n_goals * 2)


def trip_weekday(trip_date: str) -> int:
    """Weekday (0=Mon…6=Sun) of the trip, defaulting to today."""
    if trip_date:
        return _datetime.fromisoformat(trip_date).weekday()
    return _date.today().weekday()


def effective_goals(goals: list[str], familiarity: str) -> list[str]:
    """Returning visitors get hidden_gems appended to surface lesser-known spots."""
    if familiarity == "returning" and "hidden_gems" not in goals:
        return list(goals) + ["hidden_gems"]
    return list(goals)


def write_itinerary(trip: dict, route: list[dict], rag_context: str,
                    deadline: Deadline | None, degraded: list[str]) -> tuple[dict, dict]:
    """LLM prose joined onto the route, or the deterministic version if no model answers."""
    llm_meta: dict = {}
    with stage("llm"):
        try:
            raw, llm_meta = llm.complete_json(writer.build_prompt(trip, route, rag_context),
                                              deadline=deadline)
        except UpstreamError as e:
            print(f"LLM unavailable, writing itinerary from tags: {e}")
            raw = writer.fallback_raw(trip, route)
            degraded.append("llm:fallback")
    itinerary, violations = writer.assemble(raw, route, trip)
    if violations and "llm:fallback" not in degraded:
        degraded.append("llm:repaired")
        llm_meta["violations"] = [v.code for v in violations]
    return itinerary, llm_meta


def attach_stop_extras(itinerary: dict, route: list[dict], photo_map: dict[str, str]) -> None:
    """Add photo_url and opening_hours to each stop, matched by OSM id."""
    by_id = {p.get("id"): p for day in route for p in day["pois"]}
    for day in itinerary.get("days", []):
        for stop in day.get("stops", []):
            poi = by_id.get(stop.get("id"), {})
            url = photo_map.get(poi.get("name", ""))
            if url:
                stop["photo_url"] = url
            hours = poi.get("tags", {}).get("opening_hours", "")
            if hours:
                stop["opening_hours"] = hours


def run_pipeline(req: TripRequest, explain: bool = True,
                 on_progress: Callable[[str, str, int], None] | None = None) -> PipelineOutput:
    """Run the whole pipeline synchronously. Raises ValueError, NoPlacesFound or UpstreamError."""
    progress = on_progress or (lambda step, message, pct: None)
    deadline = Deadline(config.REQUEST_DEADLINE_S)
    degraded: list[str] = []

    with collect_timings() as timings, stage("pipeline"), ThreadPoolExecutor(max_workers=2) as pool:
        # Retrieval needs only the request, so it runs while we geocode and fetch POIs.
        def _retrieve() -> str:
            with stage("retrieve"):
                query = f"{req.city} {' '.join(req.goals)}"
                return format_context(retrieve(query=query, destination=req.city, top_k=5))
        rag_future = pool.submit(in_context(_retrieve))

        progress("geocoding", f"Locating {req.start_location}...", 10)
        with stage("geocode"):
            start_lat, start_lon, geo_source = geocode_with_source(
                f"{req.start_location}, {req.city}", deadline)
        if geo_source == "photon":
            degraded.append("geocode:photon")

        progress("fetching_pois", "Discovering places near you...", 25)
        start_h = START_HOUR.get(req.start_time, 9)
        with stage("fetch_pois"):
            pois, poi_source = fetch_pois_with_source(
                start_lat, start_lon, categories=effective_goals(req.goals, req.familiarity),
                radius_m=MOBILITY_RADIUS.get(req.mobility, 2000), dietary=req.dietary,
                visit_start_h=start_h, visit_end_h=min(start_h + req.duration_hours, 23),
                weekday=trip_weekday(req.trip_date), deadline=deadline,
            )
        if not pois:
            raise NoPlacesFound(f"No places found near {req.start_location}")
        if poi_source == "stale":
            degraded.append("pois:stale")

        progress("ranking", "Ranking with ML model...", 45)
        with stage("rank", candidates=len(pois)):
            ranked, rank_source = rank_pois(
                user_goals=list(req.goals), pois=[p.to_dict() for p in pois],
                top_k=top_k(req.duration_hours, req.pace, len(req.goals)), explain=explain,
                with_source=True,
            )
        if rank_source != "model":
            degraded.append("ranker:semantic")

        progress("optimizing", "Building your route...", 60)
        with stage("route"):
            route, route_meta = build_itinerary_with_meta(
                pois=ranked, n_days=n_days(req.duration_hours), transport=req.transport,
                start_lat=start_lat, start_lon=start_lon, start_hour=start_h, deadline=deadline,
            )
        if route_meta["matrix"] == "haversine_fallback":
            degraded.append("matrix:haversine")
        if route_meta["ordering"] != "ortools":
            degraded.append("route:greedy")

        progress("retrieving", "Gathering local knowledge...", 75)
        try:
            rag_context = rag_future.result(timeout=deadline.timeout(10))
        except Exception as e:
            print(f"Retrieval unavailable: {e}")
            rag_context = ""
            degraded.append("rag:unavailable")

        # Photos only need the route, so they are fetched while the LLM writes.
        def _photos() -> dict:
            with stage("photos"):
                return fetch_photos_for_itinerary(route)
        photo_future = pool.submit(in_context(_photos))

        progress("generating", "Writing your journey...", 90)
        trip = req.model_dump() | {"start_lat": start_lat, "start_lon": start_lon}
        itinerary, llm_meta = write_itinerary(trip, route, rag_context, deadline, degraded)

        try:
            photo_map = photo_future.result(timeout=min(PHOTO_WAIT_S, max(deadline.remaining(), 0.1)))
        except Exception:
            photo_map = {}
            degraded.append("photos:unavailable")
        attach_stop_extras(itinerary, route, photo_map)

        if explain:
            itinerary["ranking_explanations"] = {
                p["name"]: p["explanation"] for p in ranked if "explanation" in p}
            try:
                itinerary["global_feature_importance"] = get_ranker().feature_importance()
            except Exception:
                pass

    # explanations are large and only needed in the response, not in storage
    stored_route = [{**day, "pois": [{k: v for k, v in p.items() if k != "explanation"} for p in day["pois"]]}
                    for day in route]
    itinerary["degraded"] = degraded
    itinerary["timings_ms"] = dict(timings)
    return PipelineOutput(result=itinerary, route=stored_route, degraded=degraded,
                          timings_ms=dict(timings), llm_meta=llm_meta)
