"""
Relational schema.

users           accounts (email + bcrypt hash)
trips           every generated itinerary, with the request that produced it.
                Edits create a new row pointing at its parent, so history is
                never overwritten.
feedback        thumbs up/down on a stop, the ranker's training signal
poi_cache       last good Overpass answer per query, served stale when every
                mirror is down. Lives in the database so it survives restarts.
model_versions  one row per retraining run: metrics, and whether the candidate
                was promoted or rejected by the eval gate
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    trips: Mapped[list["Trip"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    city: Mapped[str] = mapped_column(String(200), nullable=False)
    request: Mapped[dict] = mapped_column(JSON, nullable=False)
    # The structured route (POIs with coordinates and tags) the text was written from.
    route: Mapped[list] = mapped_column(JSON, nullable=False)
    itinerary: Mapped[dict] = mapped_column(JSON, nullable=False)
    degraded: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    share_slug: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User | None] = relationship(back_populates="trips")

    __table_args__ = (Index("ix_trips_user_created", "user_id", "created_at"),)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    poi_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    poi_name: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    goals: Mapped[list] = mapped_column(JSON, default=list)
    relevant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PoiCache(Base):
    __tablename__ = "poi_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[list] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[float] = mapped_column(Float, nullable=False)  # unix seconds


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # promoted | rejected
    reason: Mapped[str] = mapped_column(Text, default="")
    feedback_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)           # candidate, held-out
    incumbent_metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # model it was compared to
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
