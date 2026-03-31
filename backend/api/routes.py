"""
FastAPI route handlers. Wires the full ML pipeline together.
"""

import json
import math
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.services.nominatim import geocode
from backend.services.overpass import fetch_pois
from backend.services.groq_client import generate_itinerary
from backend.ml.rag.retriever import retrieve, format_context
from backend.ml.ranker.scorer import rank_pois, get_ranker
from backend.ml.optimizer.scheduler import build_itinerary

router = APIRouter()

MOBILITY_RADIUS = {"easy": 1500, "moderate": 2000, "active": 3000}


PACE_STOPS_PER_DAY = {"relaxed": 4, "moderate": 6, "packed": 9}


def _n_days(duration_hours: int) -> int:
    return max(1, math.ceil(duration_hours / 10))


def _top_k(duration_hours: int, pace: str, n_goals: int) -> int:
    """Stops to select: scales with pace, always enough for each goal to appear."""
    base = PACE_STOPS_PER_DAY.get(pace, 6)
    n_days = _n_days(duration_hours)
    return max(n_days * base, n_goals * 2)


def _start_hour(start_time: str) -> int:
    return {"morning": 9, "afternoon": 13, "evening": 17}.get(start_time, 9)


def _effective_goals(goals: list[str], familiarity: str) -> list[str]:
    """Returning visitors get hidden_gems appended to surface lesser-known spots."""
    if familiarity == "returning" and "hidden_gems" not in goals:
        return goals + ["hidden_gems"]
    return goals


class TripRequest(BaseModel):
    city: str                              # area for context (e.g. "Paris")
    start_location: str                    # where you are now (e.g. "Montmartre")
    duration_hours: int = 6               # how many hours you have
    goals: list[str]                       # ["food", "nature", ...]
    transport: str = "walking"             # walking | transit
    pace: str = "moderate"                 # relaxed | moderate | packed
    budget: str = "mid"                    # free | budget | mid | splurge
    style: str = "solo"                    # solo | couple | family | group
    dietary: str = "none"                  # none | vegetarian | vegan | halal | kosher
    mobility: str = "moderate"             # easy | moderate | active
    familiarity: str = "first_time"        # first_time | returning
    start_time: str = "morning"            # morning | afternoon | evening
    notes: str = ""


class FeedbackRequest(BaseModel):
    poi_id: int
    relevant: bool
    poi_name: str = ""
    category: str = ""
    goals: list[str] = []


def _build_trip_dict(req: TripRequest, start_lat: float, start_lon: float) -> dict:
    return {
        "city": req.city,
        "start_location": req.start_location,
        "duration_hours": req.duration_hours,
        "transport": req.transport,
        "goals": req.goals,
        "pace": req.pace,
        "budget": req.budget,
        "style": req.style,
        "dietary": req.dietary,
        "mobility": req.mobility,
        "familiarity": req.familiarity,
        "start_time": req.start_time,
        "notes": req.notes,
        "start_lat": start_lat,
        "start_lon": start_lon,
    }


def _run_pipeline(req: TripRequest, explain: bool = False) -> dict:
    """Run the full itinerary pipeline synchronously. Returns result dict."""
    # 1. Geocode start location within city context
    start_lat, start_lon = geocode(f"{req.start_location}, {req.city}")

    # 2. Fetch POIs around where the user actually is
    effective_goals = _effective_goals(req.goals, req.familiarity)
    radius_m = MOBILITY_RADIUS.get(req.mobility, 2500)
    start_h = _start_hour(req.start_time)
    pois = fetch_pois(
        start_lat, start_lon, categories=effective_goals, radius_m=radius_m,
        dietary=req.dietary, visit_start_h=start_h, visit_end_h=min(start_h + req.duration_hours, 23),
    )
    if not pois:
        raise HTTPException(status_code=404, detail=f"No POIs found near {req.start_location}")

    poi_dicts = [p.to_dict() for p in pois]

    # 3. Rank POIs
    n_days = _n_days(req.duration_hours)
    ranked_pois = rank_pois(
        user_goals=req.goals, pois=poi_dicts,
        top_k=_top_k(req.duration_hours, req.pace, len(req.goals)), explain=explain,
    )

    # 4. Optimize route starting from user's location
    itinerary = build_itinerary(
        pois=ranked_pois, n_days=n_days, transport=req.transport,
        start_lat=start_lat, start_lon=start_lon,
        start_hour=_start_hour(req.start_time),
    )

    # 5. RAG retrieval
    query = f"{req.city} {' '.join(req.goals)}"
    chunks = retrieve(query=query, destination=req.city, top_k=5)
    rag_context = format_context(chunks)

    # 6. Groq synthesis
    trip = _build_trip_dict(req, start_lat, start_lon)
    result = generate_itinerary(trip=trip, itinerary=itinerary, rag_context=rag_context)

    if explain:
        result["ranking_explanations"] = {
            poi["name"]: poi["explanation"]
            for poi in ranked_pois if "explanation" in poi
        }
        try:
            result["global_feature_importance"] = get_ranker().feature_importance()
        except Exception:
            pass

    return result


@router.post("/itinerary")
async def create_itinerary(req: TripRequest):
    """Full pipeline — returns complete result with ML explanations."""
    try:
        geocode(f"{req.start_location}, {req.city}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await asyncio.to_thread(_run_pipeline, req, True)
    return result


@router.post("/itinerary/stream")
async def create_itinerary_stream(req: TripRequest):
    """SSE streaming endpoint — sends pipeline progress events then the final result."""
    async def event_generator():
        try:
            # Step 1: Geocode start location
            yield {"event": "progress", "data": json.dumps({"step": "geocoding", "message": f"Locating {req.start_location}...", "progress": 10})}
            start_lat, start_lon = await asyncio.to_thread(geocode, f"{req.start_location}, {req.city}")

            # Step 2: Fetch POIs around where the user is
            yield {"event": "progress", "data": json.dumps({"step": "fetching_pois", "message": "Discovering places near you...", "progress": 25})}
            effective_goals = _effective_goals(req.goals, req.familiarity)
            radius_m = MOBILITY_RADIUS.get(req.mobility, 2500)
            start_h = _start_hour(req.start_time)
            pois = await asyncio.to_thread(
                fetch_pois, start_lat, start_lon, effective_goals, radius_m,
                req.dietary, start_h, min(start_h + req.duration_hours, 23),
            )
            if not pois:
                yield {"event": "error", "data": json.dumps({"message": f"No places found near {req.start_location}"})}
                return
            poi_dicts = [p.to_dict() for p in pois]

            # Step 3: Rank
            yield {"event": "progress", "data": json.dumps({"step": "ranking", "message": "Ranking with ML model...", "progress": 45})}
            n_days = _n_days(req.duration_hours)
            ranked_pois = await asyncio.to_thread(rank_pois, req.goals, poi_dicts, _top_k(req.duration_hours, req.pace, len(req.goals)), True)

            # Step 4: Optimize route from start location
            yield {"event": "progress", "data": json.dumps({"step": "optimizing", "message": "Building your route...", "progress": 60})}
            itinerary = await asyncio.to_thread(
                build_itinerary, ranked_pois, n_days, req.transport,
                start_lat, start_lon, _start_hour(req.start_time),
            )

            # Step 5: RAG
            yield {"event": "progress", "data": json.dumps({"step": "retrieving", "message": "Gathering local knowledge...", "progress": 75})}
            query = f"{req.city} {' '.join(req.goals)}"
            chunks = await asyncio.to_thread(retrieve, query=query, destination=req.city, top_k=5)
            rag_context = format_context(chunks)

            # Step 6: LLM synthesis
            yield {"event": "progress", "data": json.dumps({"step": "generating", "message": "Writing your journey...", "progress": 90})}
            trip = _build_trip_dict(req, start_lat, start_lon)
            result = await asyncio.to_thread(generate_itinerary, trip, itinerary, rag_context)

            # Attach ML metadata
            result["ranking_explanations"] = {
                poi["name"]: poi["explanation"]
                for poi in ranked_pois if "explanation" in poi
            }
            try:
                result["global_feature_importance"] = get_ranker().feature_importance()
            except Exception:
                pass

            yield {"event": "result", "data": json.dumps(result)}

        except ValueError as e:
            yield {"event": "error", "data": json.dumps({"message": str(e)})}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"message": f"Pipeline error: {str(e)}"})}

    return EventSourceResponse(event_generator())


@router.get("/metrics")
async def get_metrics():
    """Return evaluation metrics history and global feature importance."""
    from backend.ml.ranker.metrics import get_metrics_history
    history = get_metrics_history()
    try:
        importance = get_ranker().feature_importance()
    except Exception:
        importance = {}
    return {"history": history, "global_feature_importance": importance}


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Receive thumbs up/down on a POI. Logs signal and triggers retraining at threshold."""
    from backend.ml.ranker.scorer import apply_feedback
    from backend.ml.ranker.feedback_store import get_feedback_count
    retrained = apply_feedback(req.poi_id, req.relevant, req.poi_name, req.category, req.goals)
    return {"status": "ok", "total_feedback": get_feedback_count(), "retrained": retrained}


@router.get("/feedback/stats")
async def feedback_stats():
    """Return feedback collection stats."""
    from backend.ml.ranker.feedback_store import get_feedback_count, get_feedback_as_training_data
    from backend.ml.ranker.retrain import RETRAIN_THRESHOLD
    count = get_feedback_count()
    return {
        "total_signals": count,
        "retrain_threshold": RETRAIN_THRESHOLD,
        "signals_until_retrain": max(0, RETRAIN_THRESHOLD - (count % RETRAIN_THRESHOLD)),
    }


@router.get("/health")
def health():
    return {"status": "ok"}
