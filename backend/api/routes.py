"""
FastAPI route handlers. Wires the full ML pipeline together.
"""

import json
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


class TripRequest(BaseModel):
    destination: str
    days: int
    transport: str          # driving | walking | cycling | transit
    goals: list[str]        # e.g. ["food", "history", "hidden gems"]
    pace: str = "moderate"  # relaxed | moderate | packed
    budget: str = "mid"     # free | budget | mid | splurge
    style: str = "solo"     # solo | couple | family | group
    notes: str = ""         # free-text preferences


class FeedbackRequest(BaseModel):
    poi_id: int
    relevant: bool
    poi_name: str = ""
    category: str = ""
    goals: list[str] = []


def _build_trip_dict(req: TripRequest) -> dict:
    return {
        "destination": req.destination,
        "days": req.days,
        "transport": req.transport,
        "goals": req.goals,
        "pace": req.pace,
        "budget": req.budget,
        "style": req.style,
        "notes": req.notes,
    }


def _run_pipeline(req: TripRequest, explain: bool = False) -> dict:
    """Run the full itinerary pipeline synchronously. Returns result dict."""
    # 1. Geocode
    lat, lon = geocode(req.destination)

    # 2. Fetch POIs
    pois = fetch_pois(lat, lon, categories=req.goals)
    if not pois:
        raise HTTPException(status_code=404, detail=f"No POIs found near {req.destination}")

    poi_dicts = [p.to_dict() for p in pois]

    # 3. Rank POIs (with optional SHAP explanations)
    ranked_pois = rank_pois(
        user_goals=req.goals, pois=poi_dicts,
        top_k=req.days * 6, explain=explain,
    )

    # 4. Optimize route
    itinerary = build_itinerary(pois=ranked_pois, n_days=req.days, transport=req.transport)

    # 5. RAG retrieval
    query = f"{req.destination} {' '.join(req.goals)}"
    chunks = retrieve(query=query, destination=req.destination, top_k=5)
    rag_context = format_context(chunks)

    # 6. Groq synthesis
    trip = _build_trip_dict(req)
    result = generate_itinerary(trip=trip, itinerary=itinerary, rag_context=rag_context)

    # Attach ML metadata
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
        lat, lon = geocode(req.destination)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await asyncio.to_thread(_run_pipeline, req, True)
    return result


@router.post("/itinerary/stream")
async def create_itinerary_stream(req: TripRequest):
    """SSE streaming endpoint — sends pipeline progress events then the final result."""
    async def event_generator():
        try:
            # Step 1: Geocode
            yield {"event": "progress", "data": json.dumps({"step": "geocoding", "message": "Geocoding destination...", "progress": 10})}
            lat, lon = await asyncio.to_thread(geocode, req.destination)

            # Step 2: Fetch POIs
            yield {"event": "progress", "data": json.dumps({"step": "fetching_pois", "message": "Fetching points of interest...", "progress": 25})}
            pois = await asyncio.to_thread(fetch_pois, lat, lon, req.goals)
            if not pois:
                yield {"event": "error", "data": json.dumps({"message": f"No POIs found near {req.destination}"})}
                return
            poi_dicts = [p.to_dict() for p in pois]

            # Step 3: Rank
            yield {"event": "progress", "data": json.dumps({"step": "ranking", "message": "Ranking with ML model...", "progress": 45})}
            ranked_pois = await asyncio.to_thread(rank_pois, req.goals, poi_dicts, req.days * 6, True)

            # Step 4: Optimize route
            yield {"event": "progress", "data": json.dumps({"step": "optimizing", "message": "Optimizing route...", "progress": 60})}
            itinerary = await asyncio.to_thread(build_itinerary, ranked_pois, req.days, req.transport)

            # Step 5: RAG
            yield {"event": "progress", "data": json.dumps({"step": "retrieving", "message": "Retrieving local knowledge...", "progress": 75})}
            query = f"{req.destination} {' '.join(req.goals)}"
            chunks = await asyncio.to_thread(retrieve, query=query, destination=req.destination, top_k=5)
            rag_context = format_context(chunks)

            # Step 6: LLM synthesis
            yield {"event": "progress", "data": json.dumps({"step": "generating", "message": "Generating descriptions...", "progress": 90})}
            trip = _build_trip_dict(req)
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
    apply_feedback(req.poi_id, req.relevant, req.poi_name, req.category, req.goals)
    return {"status": "ok", "total_feedback": get_feedback_count()}


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
