"""
FastAPI route handlers. Wires the full ML pipeline together.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.nominatim import geocode
from backend.services.overpass import fetch_pois
from backend.services.groq_client import generate_itinerary
from backend.ml.rag.retriever import retrieve, format_context
from backend.ml.ranker.scorer import rank_pois
from backend.ml.optimizer.scheduler import build_itinerary

router = APIRouter()


class TripRequest(BaseModel):
    destination: str
    days: int
    transport: str          # driving | walking | cycling | transit
    goals: list[str]        # e.g. ["food", "history", "hidden gems"]


class FeedbackRequest(BaseModel):
    poi_id: int
    relevant: bool


@router.post("/itinerary")
async def create_itinerary(req: TripRequest):
    """
    Full pipeline:
    1. Geocode destination
    2. Fetch POIs from Overpass
    3. Rank POIs with learned ranker
    4. Optimize route with VRP
    5. Retrieve RAG context
    6. Synthesize with Groq
    """
    try:
        # 1. Geocode
        lat, lon = geocode(req.destination)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Fetch POIs
    pois = fetch_pois(lat, lon, categories=req.goals)
    if not pois:
        raise HTTPException(status_code=404, detail=f"No POIs found near {req.destination}")

    poi_dicts = [p.to_dict() for p in pois]

    # 3. Rank POIs
    ranked_pois = rank_pois(user_goals=req.goals, pois=poi_dicts, top_k=req.days * 6)

    # 4. Optimize route
    itinerary = build_itinerary(
        pois=ranked_pois,
        n_days=req.days,
        transport=req.transport,
    )

    # 5. RAG retrieval
    query = f"{req.destination} {' '.join(req.goals)}"
    chunks = retrieve(query=query, destination=req.destination, top_k=5)
    rag_context = format_context(chunks)

    # 6. Groq synthesis
    trip = {
        "destination": req.destination,
        "days": req.days,
        "transport": req.transport,
        "goals": req.goals,
    }
    result = generate_itinerary(trip=trip, itinerary=itinerary, rag_context=rag_context)

    return result


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Receive thumbs up/down on a POI to improve future rankings."""
    from backend.ml.ranker.scorer import apply_feedback
    apply_feedback(req.poi_id, req.relevant)
    return {"status": "ok"}


@router.get("/health")
def health():
    return {"status": "ok"}
