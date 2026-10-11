"""
Itinerary, feedback and metrics endpoints. The pipeline itself lives in
backend/pipeline.py; this module only translates between HTTP and it.
"""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from backend.api.auth import optional_user
from backend.api.trips import save_trip
from backend.core.ratelimit import TokenBucket, limiter
from backend.core.resilience import UpstreamError
from backend.core.telemetry import aggregator, in_context
from backend.db.models import User
from backend.db.session import session_scope
from backend.pipeline import NoPlacesFound, PipelineOutput, TripRequest, run_pipeline

router = APIRouter()

# 6 itineraries in a burst, then one every 20s, per client. Each one spends
# shared LLM and Overpass quota.
itinerary_bucket = TokenBucket(capacity=6, rate=1 / 20)
feedback_bucket = TokenBucket(capacity=30, rate=1)


class FeedbackRequest(BaseModel):
    poi_id: int
    relevant: bool
    poi_name: str = Field(default="", max_length=300)
    category: str = Field(default="", max_length=40)
    goals: list[str] = Field(default_factory=list, max_length=8)


def _persist(req: TripRequest, output: PipelineOutput, user_id: str | None) -> dict:
    """Store the trip and return the response body. A storage failure never loses the itinerary."""
    result = output.result
    try:
        with session_scope() as db:
            trip = save_trip(db, user_id=user_id, request=req.model_dump(), route=output.route,
                             itinerary=result, degraded=output.degraded)
            result["trip_id"] = trip.id
    except Exception as e:
        print(f"Trip not saved: {e}")
        result["trip_id"] = None
    return result


def _http_error(e: Exception) -> HTTPException:
    if isinstance(e, NoPlacesFound):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, UpstreamError):
        return HTTPException(status_code=503, detail=f"A data source is unavailable: {e}",
                             headers={"Retry-After": "30"})
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=str(e))
    return HTTPException(status_code=500, detail="Internal error")


@router.post("/itinerary", dependencies=[Depends(limiter(itinerary_bucket))])
async def create_itinerary(req: TripRequest, user: User | None = Depends(optional_user)):
    """Run the pipeline and return the finished itinerary."""
    try:
        output = await asyncio.to_thread(run_pipeline, req)
    except Exception as e:
        if not isinstance(e, (NoPlacesFound, UpstreamError, ValueError)):
            print(f"Pipeline error: {type(e).__name__}: {e}")
        raise _http_error(e)
    return _persist(req, output, user.id if user else None)


@router.post("/itinerary/stream", dependencies=[Depends(limiter(itinerary_bucket))])
async def create_itinerary_stream(req: TripRequest, user: User | None = Depends(optional_user)):
    """
    Server-sent events: one `progress` event as each stage starts, then a
    single `result` (or `error`). The pipeline runs on a worker thread and
    hands events to the response through a queue.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    user_id = user.id if user else None

    def on_progress(step: str, message: str, pct: int) -> None:
        event = {"event": "progress", "data": json.dumps({"step": step, "message": message, "progress": pct})}
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work() -> None:
        try:
            output = run_pipeline(req, on_progress=on_progress)
            event = {"event": "result", "data": json.dumps(_persist(req, output, user_id))}
        except Exception as e:
            if not isinstance(e, (NoPlacesFound, UpstreamError, ValueError)):
                print(f"Pipeline error: {type(e).__name__}: {e}")
            event = {"event": "error", "data": json.dumps({"message": _http_error(e).detail})}
        loop.call_soon_threadsafe(queue.put_nowait, event)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    async def events():
        task = loop.run_in_executor(None, in_context(work))
        while (event := await queue.get()) is not None:
            yield event
        await task

    return EventSourceResponse(events())


@router.post("/feedback", dependencies=[Depends(limiter(feedback_bucket))])
async def submit_feedback(req: FeedbackRequest, user: User | None = Depends(optional_user)):
    """Thumbs up/down on a stop. Every RETRAIN_THRESHOLD signals a candidate model is trained and gated."""
    from backend.ml.ranker.feedback_store import get_feedback_count, log_feedback
    from backend.ml.ranker.retrain import retrain_if_needed

    def work() -> dict:
        log_feedback(req.poi_id, req.relevant, req.poi_name, req.category, req.goals,
                     user_id=user.id if user else None)
        outcome = retrain_if_needed()
        return {"status": "ok", "total_feedback": get_feedback_count(),
                "retrained": outcome == "promoted", "retrain_outcome": outcome}

    return await asyncio.to_thread(work)


@router.get("/feedback/stats")
def feedback_stats():
    from backend.ml.ranker.feedback_store import get_feedback_count
    from backend.ml.ranker.retrain import RETRAIN_THRESHOLD
    count = get_feedback_count()
    return {
        "total_signals": count,
        "retrain_threshold": RETRAIN_THRESHOLD,
        "signals_until_retrain": RETRAIN_THRESHOLD - (count % RETRAIN_THRESHOLD),
    }


@router.get("/metrics")
def get_metrics():
    """Ranker versions (with the eval-gate verdict on each) and feature importance."""
    from backend.ml.ranker.registry import history
    from backend.ml.ranker.scorer import get_ranker
    try:
        importance = get_ranker().feature_importance()
    except Exception:
        importance = {}
    return {"history": history(), "global_feature_importance": importance}


@router.get("/metrics/latency")
def latency_metrics():
    """p50/p95 per pipeline stage over the last 500 spans of each."""
    return aggregator.summary()


@router.get("/health")
def health():
    """Liveness plus the state of each dependency the pipeline can degrade around."""
    from backend.services import llm, nominatim, overpass
    from backend.ml.optimizer import distance
    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as e:
        database = f"error: {type(e).__name__}"
    breakers = {
        "nominatim": nominatim._nominatim_breaker.state,
        "photon": nominatim._photon_breaker.state,
        "openrouteservice": distance._ors_breaker.state,
        **{f"overpass:{m.split('/')[2]}": b.state for m, b in overpass._breakers.items()},
        **{f"llm:{m}": b.state for m, b in llm._breakers.items()},
    }
    return {"status": "ok" if database == "ok" else "degraded", "database": database, "breakers": breakers}
