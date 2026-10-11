"""
Trip editing endpoint. The agent itself lives in backend/agent/; this module
only translates between HTTP and it, the same split as routes.py and pipeline.py.

An edit never overwrites a trip. It is stored as a new row whose parent_id
points at the trip it was made from, so the traveller can always go back.
"""

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.agent.service import edit_trip
from backend.api.auth import optional_user
from backend.api.routes import _http_error
from backend.api.trips import load_trip, save_trip
from backend.core.ratelimit import TokenBucket, limiter
from backend.core.resilience import UpstreamError
from backend.db.models import User
from backend.db.session import get_db

router = APIRouter(tags=["agent"])

# 5 edits in a burst, then one every 15s, per client. An edit is several model
# calls, so it is limited separately from (and tighter than) itinerary creation.
edit_bucket = TokenBucket(capacity=5, rate=1 / 15)


class EditRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)

    @field_validator("message")
    @classmethod
    def _no_nul(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("must not contain NUL bytes")  # Postgres rejects them in text and JSON
        return v


@router.post("/trips/{trip_id}/edit", dependencies=[Depends(limiter(edit_bucket))])
async def edit_trip_endpoint(trip_id: str, body: EditRequest, user: User | None = Depends(optional_user),
                             db: Session = Depends(get_db)):
    """Edit a trip from a plain-language request. Returns the new itinerary, or the old one if nothing changed."""
    trip = load_trip(db, trip_id, user, write=True)
    db.commit()  # do not hold a database connection while the model thinks
    try:
        output = await asyncio.to_thread(edit_trip, trip.request, trip.route, trip.itinerary, body.message)
    except Exception as e:
        if not isinstance(e, (UpstreamError, ValueError)):
            print(f"Edit error: {type(e).__name__}: {e}")
        raise _http_error(e)

    extra = {"changes": output.changes, "summary": output.summary, "steps": output.run.steps}
    if not output.changed:
        return trip.itinerary | {"trip_id": trip.id} | extra
    new_trip = save_trip(db, user_id=trip.user_id, request=trip.request, route=output.route,
                         itinerary=output.result, degraded=output.degraded, parent_id=trip.id)
    db.commit()  # the new trip must be readable by the time the client has its id
    return output.result | {"trip_id": new_trip.id} | extra
