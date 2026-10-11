"""
Model registry and promotion gate.

Retraining on user feedback can make the ranker worse as easily as better: a
handful of thumbs from one user, a bug in how feedback is turned into rows, or
someone clicking at random all produce a model that trains without error. So a
retrained model is only a CANDIDATE. It replaces the serving model (the
incumbent) only if it holds up on a fixed held-out set that neither model was
trained on (trainer.HOLDOUT_CITIES).

The rule: promote if candidate NDCG@10 >= incumbent NDCG@10 - TOLERANCE.

Why a tolerance instead of "must be better": the held-out set is a few queries,
so two equally good models differ by a little noise, and feedback is meant to
pull the model toward what real users like, which is not exactly what the
held-out labels (an LLM judge's opinion) say. The gate is there to stop
regressions, not to demand that every retrain wins on the judge's labels.

Every run is written to the model_versions table whether it was promoted or
rejected, with both models' metrics and the reason. That table is also where
"how much feedback had we seen at the last run" comes from, so the retrain
schedule survives restarts and is shared by every process on the same database.
"""

from sqlalchemy import select

from backend.db.models import ModelVersion
from backend.db.session import session_scope

GATE_METRIC = "ndcg@10"
TOLERANCE = 0.01


def decide(candidate_metrics: dict, incumbent_metrics: dict) -> tuple[str, str]:
    """The gate. Returns (status, reason) with status "promoted" or "rejected"."""
    candidate = candidate_metrics[GATE_METRIC]
    if GATE_METRIC not in incumbent_metrics:
        return "promoted", f"no usable incumbent; candidate {GATE_METRIC} {candidate:.4f}"
    incumbent = incumbent_metrics[GATE_METRIC]
    detail = (f"candidate {GATE_METRIC} {candidate:.4f} vs incumbent {incumbent:.4f} "
              f"(tolerance {TOLERANCE})")
    if candidate >= incumbent - TOLERANCE:
        return "promoted", detail
    return "rejected", f"regression on held-out set: {detail}"


def promote(ranker) -> None:
    """Make `ranker` the serving model: on disk first (atomically), then in memory."""
    from . import scorer
    ranker.save()
    scorer.set_ranker(ranker)


def record(status: str, reason: str, feedback_count: int,
           metrics: dict | None = None, incumbent_metrics: dict | None = None) -> None:
    """Write one retraining run to the model_versions table."""
    with session_scope() as db:
        db.add(ModelVersion(status=status, reason=reason, feedback_count=feedback_count,
                            metrics=metrics or {}, incumbent_metrics=incumbent_metrics or {}))


def last_feedback_count() -> int:
    """Feedback rows that existed at the most recent retraining run (0 if there has been none)."""
    with session_scope() as db:
        count = db.scalar(select(ModelVersion.feedback_count).order_by(ModelVersion.id.desc()).limit(1))
        return count or 0


def history() -> list[dict]:
    """Every retraining run, oldest first."""
    with session_scope() as db:
        rows = db.scalars(select(ModelVersion).order_by(ModelVersion.id)).all()
        return [
            {"id": r.id, "status": r.status, "reason": r.reason, "feedback_count": r.feedback_count,
             "metrics": r.metrics or {}, "incumbent_metrics": r.incumbent_metrics or {},
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ]
