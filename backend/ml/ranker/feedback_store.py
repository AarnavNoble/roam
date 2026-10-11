"""
Feedback signals (thumbs up/down on a stop), stored in the feedback table.
They are the ranker's online training data.
"""

from sqlalchemy import func, select

from backend.db.models import Feedback
from backend.db.session import session_scope


def log_feedback(poi_id: int, relevant: bool, poi_name: str = "", category: str = "",
                 goals: list[str] | None = None, user_id: str | None = None) -> None:
    """Store a feedback signal."""
    with session_scope() as db:
        db.add(Feedback(poi_id=poi_id, relevant=relevant, poi_name=poi_name,
                        category=category, goals=list(goals or []), user_id=user_id))


def get_feedback_count() -> int:
    """Return total number of feedback signals collected."""
    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(Feedback)) or 0


def get_feedback_as_training_data() -> list[dict]:
    """Return all feedback signals as structured dicts for retraining."""
    with session_scope() as db:
        rows = db.scalars(select(Feedback).order_by(Feedback.id)).all()
        return [
            {"poi_id": r.poi_id, "poi_name": r.poi_name, "category": r.category,
             "goals": list(r.goals or []), "relevant": bool(r.relevant)}
            for r in rows
        ]
