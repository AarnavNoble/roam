"""
Saved trips and share links.

Every generated itinerary is stored. A signed-in user's trips are private to
them. An anonymous trip belongs to nobody and can be read by anyone holding
its id, which is 128 random bits. Sharing mints a separate short slug, so a
link can be revoked without deleting the trip and never exposes the trip id.
Revoking needs an owner: an anonymous trip's share link stays live until a
signed-in user claims the trip (by sharing or editing it) and unshares it.

Handlers that write commit before they return. The session dependency also
commits, but only after the response has been sent, which is too late for a
client that acts on the response straight away.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.auth import current_user, optional_user
from backend.db.models import Trip, User
from backend.db.session import get_db

router = APIRouter(tags=["trips"])


def save_trip(db: Session, *, user_id: str | None, request: dict, route: list, itinerary: dict,
              degraded: list, parent_id: str | None = None) -> Trip:
    # explanations and timings describe one response, not the trip
    stored = {k: v for k, v in itinerary.items()
              if k not in ("ranking_explanations", "global_feature_importance", "timings_ms", "trip_id")}
    trip = Trip(user_id=user_id, parent_id=parent_id, city=request["city"], request=request,
                route=route, itinerary=stored, degraded=list(degraded))
    db.add(trip)
    db.flush()
    return trip


def _utc_iso(dt: datetime) -> str:
    # SQLite hands back naive datetimes; they were stored as UTC
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _summary(trip: Trip) -> dict:
    return {
        "id": trip.id, "city": trip.city, "goals": trip.request.get("goals", []),
        "trip_date": trip.request.get("trip_date", ""), "created_at": _utc_iso(trip.created_at),
        "stops": sum(len(d.get("stops", [])) for d in trip.itinerary.get("days", [])),
        "share_slug": trip.share_slug, "parent_id": trip.parent_id,
    }


def _detail(trip: Trip) -> dict:
    return _summary(trip) | {"request": trip.request, "itinerary": trip.itinerary | {"trip_id": trip.id}}


def load_trip(db: Session, trip_id: str, user: User | None, write: bool = False) -> Trip:
    """
    Fetch a trip the caller may access. A trip owned by someone else is
    reported as missing rather than forbidden, so ids cannot be probed.
    """
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    if trip.user_id is not None and (user is None or user.id != trip.user_id):
        raise HTTPException(status_code=404, detail="Trip not found")
    if write and trip.user_id is None and user is not None:
        trip.user_id = user.id  # first signed-in writer claims an anonymous trip
    return trip


@router.get("/trips")
def list_trips(user: User = Depends(current_user), db: Session = Depends(get_db),
               limit: int = 50, offset: int = 0):
    rows = db.scalars(
        select(Trip).where(Trip.user_id == user.id)
        .order_by(Trip.created_at.desc()).limit(min(max(limit, 1), 100)).offset(max(offset, 0))
    ).all()
    return {"trips": [_summary(t) for t in rows]}


@router.get("/trips/{trip_id}")
def get_trip(trip_id: str, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    return _detail(load_trip(db, trip_id, user))


@router.delete("/trips/{trip_id}", status_code=204)
def delete_trip(trip_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = load_trip(db, trip_id, user)
    if trip.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trip not found")
    db.delete(trip)
    db.commit()


@router.post("/trips/{trip_id}/share")
def share_trip(trip_id: str, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """Create (or return the existing) public read-only link for a trip."""
    trip = load_trip(db, trip_id, user, write=True)
    if trip.share_slug is None:
        trip.share_slug = secrets.token_urlsafe(8)[:11]
    db.commit()  # the link must work by the time the client has it
    return {"share_slug": trip.share_slug, "path": f"/t/{trip.share_slug}"}


@router.delete("/trips/{trip_id}/share", status_code=204)
def unshare_trip(trip_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = load_trip(db, trip_id, user)
    if trip.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip.share_slug = None
    db.commit()


@router.get("/shared/{slug}")
def get_shared_trip(slug: str, db: Session = Depends(get_db)):
    """Public, read-only. Returns the itinerary only: no owner, no request notes."""
    trip = db.scalar(select(Trip).where(Trip.share_slug == slug))
    if trip is None:
        raise HTTPException(status_code=404, detail="Shared trip not found")
    return {"city": trip.city, "goals": trip.request.get("goals", []),
            "trip_date": trip.request.get("trip_date", ""), "itinerary": trip.itinerary}
