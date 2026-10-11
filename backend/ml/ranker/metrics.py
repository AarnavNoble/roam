"""
Evaluation metrics for the POI ranker.

This is the only implementation of NDCG@k in the project. The benchmark, the
trainer and the promotion gate all import it, so a number quoted from one is
comparable with a number from another.

NDCG@k here means: sort the WHOLE candidate list by predicted score, take the
top k, and compare their discounted gain with the best possible top k. (An
earlier version sliced the first k candidates in their original order before
scoring, which measures how well k arbitrary items are ordered among
themselves and says nothing about whether the best items were found.)

Gain is 2^label - 1, the same gain LightGBM's lambdarank optimises, so a
label-4 place counts 15 times a label-1 place.
"""

import numpy as np
from sklearn.metrics import ndcg_score

KS = [5, 10]
RELEVANT_THRESHOLD = 3.0   # labels run 0 to 4; 3 and up is "a stop you would want"


def ndcg_at_k(y_true: np.ndarray, y_score: np.ndarray, k: int) -> float:
    """
    NDCG@k for a single query group, over the full candidate list.
    Tied scores get the average of their possible orderings (sklearn does this),
    so a model that emits the same score for everything earns no credit from the
    order the candidates happened to arrive in. A query with no relevant
    candidate scores 0.
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_score = np.asarray(y_score, dtype=np.float64)
    if len(y_true) < 2:
        return 1.0 if len(y_true) == 1 and y_true[0] > 0 else 0.0
    gains = 2.0 ** y_true - 1.0
    return float(ndcg_score(gains[None, :], y_score[None, :], k=k))


def precision_at_k(y_true: np.ndarray, y_score: np.ndarray, k: int,
                   threshold: float = RELEVANT_THRESHOLD) -> float:
    """Precision@k: fraction of top-k predictions that are relevant."""
    top_k = min(k, len(y_score))
    top_k_indices = np.argsort(y_score)[::-1][:top_k]
    relevant = sum(1 for i in top_k_indices if y_true[i] >= threshold)
    return relevant / top_k


def evaluate_scores(y: np.ndarray, scores: np.ndarray, groups: list[int],
                    ks: list[int] = None) -> dict:
    """
    Mean NDCG@k and precision@k over query groups, for any vector of scores.
    groups: number of candidates per query, in row order (sums to len(y)).
    """
    ks = ks or KS
    per_query = {f"{m}@{k}": [] for k in ks for m in ("ndcg", "precision")}
    offset = 0
    for g in groups:
        y_true, y_score = y[offset:offset + g], scores[offset:offset + g]
        for k in ks:
            per_query[f"ndcg@{k}"].append(ndcg_at_k(y_true, y_score, k))
            per_query[f"precision@{k}"].append(precision_at_k(y_true, y_score, k))
        offset += g
    return {name: float(np.mean(vals)) if vals else 0.0 for name, vals in per_query.items()}


def evaluate_ranker(ranker, X: np.ndarray, y: np.ndarray, groups: list[int], ks: list[int] = None) -> dict:
    """Compute NDCG@k and precision@k for a trained ranker across all query groups."""
    return evaluate_scores(y, ranker.score(X), groups, ks)
