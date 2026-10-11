"""
Retraining on user feedback, behind an eval gate.

Every RETRAIN_THRESHOLD new feedback rows, a candidate model is trained on the
base labelled set plus all feedback so far. It is then scored, alongside the
model currently serving, on the fixed held-out cities, and registry.decide()
says whether it may replace it. Either way the run is recorded in the
model_versions table (see registry.py for the rule and the reasoning).

What changed from the first version, and why:
  - it evaluated the new model on its own training data, which every model
    passes, and swapped it in unconditionally;
  - it kept "feedback count at last train" in a module global, which reset on
    restart and was different in every worker. It is now read from the
    model_versions table;
  - two feedback requests arriving together could both retrain. A lock now
    lets one through and the other returns "skipped". The lock is per process:
    with several workers, two could still train at once, and both would write
    the model file atomically, so the worst case is wasted work, not a corrupt
    file.
"""

import threading
from collections import defaultdict

import numpy as np

from . import registry
from .features import extract_features  # before model: see the note in model.py
from .feedback_store import get_feedback_as_training_data, get_feedback_count
from .metrics import evaluate_ranker
from .model import POIRanker
from .trainer import load_base_data

RETRAIN_THRESHOLD = 10  # retrain after every 10 new feedback signals

_retrain_lock = threading.Lock()


def build_feedback_training_data() -> tuple[np.ndarray, np.ndarray, list[int]] | None:
    """
    Convert feedback signals into (X, y, groups) for LightGBM.
    Groups feedback by goals so the ranker learns relative ordering.
    """
    signals = get_feedback_as_training_data()
    if not signals:
        return None

    # Group signals by goals string so ranker sees them as a query group
    goal_groups: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        key = ",".join(sorted(s["goals"]))
        goal_groups[key].append(s)

    X_parts, y_parts, groups = [], [], []
    for goal_str, items in goal_groups.items():
        if len(items) < 2:
            continue  # need at least 2 items per group for ranking

        goals = goal_str.split(",")
        pois = [
            {"name": s["poi_name"], "category": s["category"],
             "description": f"{s['poi_name']} {s['category']}", "tags": {}}
            for s in items
        ]
        features = extract_features(goals, pois)
        # thumbs up maps to "good stop" (3), thumbs down to "not relevant" (0)
        relevance = np.array([3 if s["relevant"] else 0 for s in items], dtype=np.float32)

        X_parts.append(features)
        y_parts.extend(relevance)
        groups.append(len(items))

    if not X_parts:
        return None

    return np.vstack(X_parts), np.array(y_parts, dtype=np.float32), groups


def retrain_if_needed() -> str:
    """
    Train and gate a candidate if RETRAIN_THRESHOLD feedback rows have arrived
    since the last run. Returns "skipped", "promoted" or "rejected".
    """
    if not _retrain_lock.acquire(blocking=False):
        return "skipped"  # another request is retraining right now
    try:
        feedback_count = get_feedback_count()
        if feedback_count - registry.last_feedback_count() < RETRAIN_THRESHOLD:
            return "skipped"
        try:
            return _train_and_gate(feedback_count)
        except Exception as e:
            # A failed run is recorded too. Otherwise every later feedback
            # request would hit the same failure again.
            print(f"Retraining failed: {e}")
            registry.record("rejected", f"training failed: {type(e).__name__}: {e}", feedback_count)
            return "rejected"
    finally:
        _retrain_lock.release()


def _train_and_gate(feedback_count: int) -> str:
    from . import scorer

    (X, y, groups), (X_hold, y_hold, groups_hold) = load_base_data()
    feedback = build_feedback_training_data()
    if feedback:
        X_fb, y_fb, groups_fb = feedback
        X = np.vstack([X, X_fb])
        y = np.concatenate([y, y_fb])
        groups = groups + groups_fb

    candidate = POIRanker()
    candidate.train(X, y, groups)
    candidate_metrics = evaluate_ranker(candidate, X_hold, y_hold, groups_hold)

    try:
        incumbent_metrics = evaluate_ranker(scorer.get_ranker(), X_hold, y_hold, groups_hold)
    except Exception as e:
        # No model file yet, or one saved for an older feature set
        print(f"Incumbent could not be scored ({e})")
        incumbent_metrics = {}

    status, reason = registry.decide(candidate_metrics, incumbent_metrics)
    if status == "promoted":
        registry.promote(candidate)
    registry.record(status, reason, feedback_count, candidate_metrics, incumbent_metrics)
    print(f"Retrain with {feedback_count} feedback signals: {status} ({reason})")
    return status
