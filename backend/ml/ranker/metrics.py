"""
Evaluation metrics for the POI ranker.
Tracks NDCG@k and precision@k, logs before/after retraining.
"""

import json
import time
import numpy as np
from pathlib import Path
from sklearn.metrics import ndcg_score

METRICS_LOG = Path(__file__).resolve().parents[3] / "data" / "metrics_log.json"


def ndcg_at_k(y_true: np.ndarray, y_score: np.ndarray, k: int) -> float:
    """NDCG@k for a single query group."""
    if len(y_true) < 2:
        return 1.0
    top_k = min(k, len(y_true))
    return float(ndcg_score([y_true[:top_k]], [y_score[:top_k]]))


def precision_at_k(y_true: np.ndarray, y_score: np.ndarray, k: int, threshold: float = 2.0) -> float:
    """Precision@k: fraction of top-k predictions that are relevant."""
    top_k = min(k, len(y_score))
    top_k_indices = np.argsort(y_score)[::-1][:top_k]
    relevant = sum(1 for i in top_k_indices if y_true[i] >= threshold)
    return relevant / top_k


def evaluate_ranker(ranker, X: np.ndarray, y: np.ndarray, groups: list[int], ks: list[int] = None) -> dict:
    """Compute NDCG@k and precision@k across all query groups."""
    if ks is None:
        ks = [5, 10]

    results = {}
    offset = 0
    ndcgs = {k: [] for k in ks}
    precisions = {k: [] for k in ks}

    for g in groups:
        y_true = y[offset:offset + g]
        y_score = ranker.score(X[offset:offset + g])
        for k in ks:
            if g >= k:
                ndcgs[k].append(ndcg_at_k(y_true, y_score, k))
                precisions[k].append(precision_at_k(y_true, y_score, k))
        offset += g

    for k in ks:
        results[f"ndcg@{k}"] = float(np.mean(ndcgs[k])) if ndcgs[k] else 0.0
        results[f"precision@{k}"] = float(np.mean(precisions[k])) if precisions[k] else 0.0

    return results


def log_metrics(metrics: dict, phase: str = "post_retrain") -> dict:
    """Append metrics to JSON log file."""
    METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": time.time(), "phase": phase, **metrics}
    log = []
    if METRICS_LOG.exists():
        try:
            log = json.loads(METRICS_LOG.read_text())
        except json.JSONDecodeError:
            log = []
    log.append(entry)
    METRICS_LOG.write_text(json.dumps(log, indent=2))
    return entry


def get_metrics_history() -> list[dict]:
    """Return all logged metric entries."""
    if METRICS_LOG.exists():
        try:
            return json.loads(METRICS_LOG.read_text())
        except json.JSONDecodeError:
            return []
    return []
