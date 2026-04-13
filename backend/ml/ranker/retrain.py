"""
Incremental retraining: combines original synthetic training data with
real user feedback signals to improve the ranker over time.

Called automatically when enough feedback has been collected (RETRAIN_THRESHOLD).
"""

import numpy as np
from .feedback_store import get_feedback_as_training_data, get_feedback_count
from .features import extract_features
from .model import POIRanker
from .trainer import generate_training_data

RETRAIN_THRESHOLD = 10  # retrain after every 10 new feedback signals

def _init_last_train_count() -> int:
    """
    Initialize the baseline feedback count on startup.
    Round down to the nearest completed threshold so we don't spuriously
    retrain immediately after a server restart.
    """
    try:
        count = get_feedback_count()
        return (count // RETRAIN_THRESHOLD) * RETRAIN_THRESHOLD
    except Exception:
        return 0

_feedback_count_at_last_train = _init_last_train_count()


def build_feedback_training_data() -> tuple[np.ndarray, np.ndarray, list[int]] | None:
    """
    Convert feedback signals into (X, y, groups) for LightGBM.
    Groups feedback by goals so the ranker learns relative ordering.
    """
    signals = get_feedback_as_training_data()
    if not signals:
        return None

    # Group signals by goals string so ranker sees them as a query group
    from collections import defaultdict
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
        relevance = np.array([3 if s["relevant"] else 0 for s in items], dtype=np.float32)

        X_parts.append(features)
        y_parts.extend(relevance)
        groups.append(len(items))

    if not X_parts:
        return None

    return np.vstack(X_parts), np.array(y_parts, dtype=np.float32), groups


def retrain_if_needed() -> bool:
    """
    Check if enough new feedback has arrived to trigger retraining.
    Returns True if retraining occurred.
    """
    global _feedback_count_at_last_train
    current_count = get_feedback_count()

    if current_count - _feedback_count_at_last_train < RETRAIN_THRESHOLD:
        return False

    print(f"Retraining ranker with {current_count} feedback signals...")

    # Base: synthetic training data
    X_base, y_base, groups_base = generate_training_data()

    # Augment with real feedback
    feedback_data = build_feedback_training_data()
    if feedback_data:
        X_fb, y_fb, groups_fb = feedback_data
        X = np.vstack([X_base, X_fb])
        y = np.concatenate([y_base, y_fb])
        groups = groups_base + groups_fb
    else:
        X, y, groups = X_base, y_base, groups_base

    # Evaluate BEFORE retraining
    from .metrics import evaluate_ranker, log_metrics
    from . import scorer as scorer_mod
    try:
        old_ranker = scorer_mod.get_ranker()
        pre_metrics = evaluate_ranker(old_ranker, X, y, groups)
        log_metrics(pre_metrics, phase="pre_retrain")
        print(f"Pre-retrain metrics: {pre_metrics}")
    except Exception:
        pass  # first train, no old model

    ranker = POIRanker()
    ranker.train(X, y, groups)
    ranker.save()

    # Evaluate AFTER retraining
    post_metrics = evaluate_ranker(ranker, X, y, groups)
    log_metrics(post_metrics, phase="post_retrain")
    print(f"Post-retrain metrics: {post_metrics}")

    # Reload the global ranker instance
    scorer_mod._ranker = ranker

    _feedback_count_at_last_train = current_count
    print(f"Retraining complete. Model updated.")
    return True
