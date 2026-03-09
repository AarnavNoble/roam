"""
Runtime POI scoring: ranks a list of POIs against user goals.
Used by the API at request time.
"""

import numpy as np
from .features import extract_features
from .model import POIRanker

_ranker = None


def get_ranker() -> POIRanker:
    global _ranker
    if _ranker is None:
        _ranker = POIRanker.load()
    return _ranker


def rank_pois(user_goals: list[str], pois: list[dict], top_k: int = 20) -> list[dict]:
    """
    Score and rank a list of POIs against user goals.
    Returns top_k POIs sorted by relevance score.
    """
    if not pois:
        return []

    ranker = get_ranker()
    features = extract_features(user_goals, pois)
    scores = ranker.score(features)
    ranked = ranker.rank_pois(pois, scores)
    return ranked[:top_k]


def apply_feedback(poi_id: int, relevant: bool) -> None:
    """
    Placeholder for online learning feedback loop.
    Logs positive/negative signals to retrain the ranker incrementally.
    """
    # TODO: log feedback to DB and periodically retrain
    signal = "positive" if relevant else "negative"
    print(f"Feedback received: POI {poi_id} → {signal}")
