"""
Runtime POI scoring: ranks a list of POIs against user goals.
Used by the API at request time.
"""

import threading

from .model import POIRanker

_ranker = None
_ranker_lock = threading.Lock()


def get_ranker() -> POIRanker:
    global _ranker
    with _ranker_lock:
        if _ranker is None:
            _ranker = POIRanker.load()
        return _ranker


def set_ranker(ranker: POIRanker) -> None:
    """Swap the serving model. Requests in flight keep the instance they already hold."""
    global _ranker
    with _ranker_lock:
        _ranker = ranker


def rank_pois(user_goals: list[str], pois: list[dict], top_k: int = 20, explain: bool = False,
              with_source: bool = False):
    """
    Score and rank a list of POIs against user goals.
    Returns top_k POIs sorted by relevance score.
    If explain=True, attaches per-POI feature values and SHAP contributions.
    If with_source=True, returns (pois, source): "model" normally, "semantic"
    when no trained model could be loaded and POIs were ordered by embedding
    similarity alone.
    """
    if not pois:
        return ([], "model") if with_source else []

    from .features import extract_features_explained
    features, feature_dicts = extract_features_explained(user_goals, pois)

    source = "model"
    try:
        ranker = get_ranker()
        scores = ranker.score(features)
        if explain:
            for poi, feat_dict, contrib in zip(pois, feature_dicts, ranker.explain(features)):
                poi["explanation"] = {"features": feat_dict, "contributions": contrib}
        ranked = ranker.rank_pois(pois, scores)
    except Exception as e:
        # No model file, or one written for a different feature set. Ranking by
        # the semantic feature alone is the baseline the model has to beat.
        print(f"Ranker unavailable ({e}), ranking by semantic similarity")
        source = "semantic"
        order = sorted(range(len(pois)), key=lambda i: features[i][0], reverse=True)
        ranked = [{**pois[i], "relevance_score": float(features[i][0])} for i in order]

    # Enforce category diversity so one category cannot crowd out the rest
    diverse = _diversify(ranked, top_k, user_goals)
    return (diverse, source) if with_source else diverse


def _diversify(ranked: list[dict], top_k: int, user_goals: list[str]) -> list[dict]:
    """
    Pick top_k POIs guaranteeing at least 1 stop per user goal, then fill
    remaining slots by score with a per-category cap.

    adventure/hidden_gems POIs are classified as 'attraction' by OSM inference,
    so we map those goals to the right bucket.
    """
    GOAL_TO_CAT = {
        "food": "food", "nature": "nature", "history": "history",
        "culture": "culture", "nightlife": "nightlife", "shopping": "shopping",
        "adventure": "attraction", "hidden_gems": "attraction",
    }

    # Group ranked POIs by inferred category (preserves score order within each group)
    by_cat: dict[str, list] = {}
    for poi in ranked:
        by_cat.setdefault(poi.get("category", "attraction"), []).append(poi)

    selected: list[dict] = []
    used_ids: set = set()

    # Phase 1: guarantee 1 best-ranked POI per user goal
    for goal in user_goals:
        cat = GOAL_TO_CAT.get(goal, goal)
        for poi in by_cat.get(cat, []):
            if poi.get("id") not in used_ids:
                selected.append(poi)
                used_ids.add(poi.get("id"))
                break  # only take the top-ranked one per goal

    # Phase 2: fill remaining slots by score, max 2 per category
    max_per_cat = max(2, top_k // max(len(user_goals), 1))
    cat_counts: dict[str, int] = {}
    for poi in selected:
        cat_counts[poi.get("category", "attraction")] = cat_counts.get(poi.get("category", "attraction"), 0) + 1

    for poi in ranked:
        if len(selected) >= top_k:
            break
        pid = poi.get("id")
        if pid in used_ids:
            continue
        cat = poi.get("category", "attraction")
        if cat_counts.get(cat, 0) < max_per_cat:
            selected.append(poi)
            used_ids.add(pid)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    return selected[:top_k]


def apply_feedback(poi_id: int, relevant: bool, poi_name: str = "", category: str = "", goals: list[str] = None) -> bool:
    """
    Log feedback signal and trigger retraining if threshold is reached.
    Returns True if retraining occurred.
    """
    from .feedback_store import log_feedback
    from .retrain import retrain_if_needed

    log_feedback(poi_id=poi_id, relevant=relevant, poi_name=poi_name, category=category, goals=goals)
    print(f"Feedback logged: POI {poi_id} ({poi_name}) → {'relevant' if relevant else 'not relevant'}")
    return retrain_if_needed()
