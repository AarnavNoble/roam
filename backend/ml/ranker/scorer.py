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


def rank_pois(user_goals: list[str], pois: list[dict], top_k: int = 20, explain: bool = False) -> list[dict]:
    """
    Score and rank a list of POIs against user goals.
    Returns top_k POIs sorted by relevance score.
    If explain=True, attaches per-POI feature values and SHAP contributions.
    """
    if not pois:
        return []

    ranker = get_ranker()

    if explain:
        from .features import extract_features_explained
        features, feature_dicts = extract_features_explained(user_goals, pois)
        scores = ranker.score(features)
        contributions = ranker.explain(features)

        # Attach explanation to each POI before sorting
        for poi, feat_dict, contrib in zip(pois, feature_dicts, contributions):
            poi["explanation"] = {"features": feat_dict, "contributions": contrib}
    else:
        features = extract_features(user_goals, pois)
        scores = ranker.score(features)

    ranked = ranker.rank_pois(pois, scores)
    return ranked[:top_k]


def apply_feedback(poi_id: int, relevant: bool, poi_name: str = "", category: str = "", goals: list[str] = None) -> None:
    """
    Log feedback signal and trigger retraining if threshold is reached.
    """
    from .feedback_store import log_feedback
    from .retrain import retrain_if_needed

    log_feedback(poi_id=poi_id, relevant=relevant, poi_name=poi_name, category=category, goals=goals)
    print(f"Feedback logged: POI {poi_id} ({poi_name}) → {'relevant' if relevant else 'not relevant'}")
    retrain_if_needed()
