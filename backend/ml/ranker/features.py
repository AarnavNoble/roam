"""
Feature extraction for POI ranking.

The ranker has to answer "given these goals, which of these candidates first?"
from what OpenStreetMap gives us: a name, a handful of tags, and the category
we inferred from them. Embedding similarity alone (column 0) already orders
candidates by topic. The other columns carry what an embedding of a short tag
string cannot: whether the place is in a category the user asked for at all,
and whether it is a place people actually go to (OSM mappers add a wikipedia
link, a website and opening hours to places that matter).

Column 0 must stay the semantic score: scorer.py falls back to it when no model
is loaded, and the benchmark's cosine_only baseline reads it.

Importing this module pulls in torch (through sentence_transformers). On macOS
that has to happen before lightgbm is imported, or lightgbm fails to load
libomp, which is why model.py imports this module first.
"""

import re

import numpy as np
from backend.ml.rag.embedder import embed_texts, embed_query

# name -> why the feature is there. The order is the column order.
FEATURES = {
    "semantic_score":  "cosine similarity between the goals and the POI description: topical match",
    "semantic_rank":   "that score's percentile among this request's candidates: cosine scales differ per query, ranks do not",
    "category_match":  "POI is in a category one of the goals asks for: the embedding misses this when the name is opaque",
    "cuisine_match":   "food goal and food POI: lets the model weight each goal/category pair on its own",
    "nature_match":    "nature goal and nature POI",
    "history_match":   "history goal and historic POI",
    "culture_match":   "culture goal and museum, gallery or theatre",
    "nightlife_match": "nightlife goal and bar, pub or club",
    "shopping_match":  "shopping goal and shop",
    "adventure_match": "adventure or hidden-gems goal and a tourism attraction",
    "is_notable":      "has a wikipedia or wikidata tag: someone wrote an encyclopedia entry about it",
    "tag_count_norm":  "number of OSM tags, capped: well-mapped places tend to be established ones",
    "has_details":     "has a website or opening hours: an operating venue rather than a stale map entry",
    "is_tourism":      "carries a tourism=* tag: mapped as a visitor destination, not just a local amenity",
}
FEATURE_NAMES = list(FEATURES)

# feature -> (words that signal the goal, POI categories that satisfy it)
GOAL_RULES = {
    "cuisine_match":   (("food", "eat", "restaurant", "cuisine", "dinner", "lunch"), ("food",)),
    "nature_match":    (("nature", "park", "outdoor", "hiking", "beach"), ("nature",)),
    "history_match":   (("history", "historic", "heritage", "ruins"), ("history",)),
    "culture_match":   (("culture", "museum", "art", "galler"), ("culture",)),
    "nightlife_match": (("nightlife", "bar", "club", "party", "drinks"), ("nightlife",)),
    "shopping_match":  (("shopping", "shop", "market", "mall", "retail"), ("shopping",)),
    "adventure_match": (("adventure", "theme park", "zoo", "aquarium", "thrill", "hidden gem"), ("attraction",)),
}


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two normalized vectors."""
    return float(np.dot(a, b))


def _percentile_ranks(scores: np.ndarray) -> np.ndarray:
    """1.0 for the highest score down to 0.0 for the lowest (1.0 when there is only one)."""
    n = len(scores)
    if n == 1:
        return np.ones(1, dtype=np.float32)
    ranks = np.argsort(np.argsort(scores))
    return (ranks / (n - 1)).astype(np.float32)


def extract_features(user_goals: list[str], pois: list[dict]) -> np.ndarray:
    """
    Build feature matrix (N_pois, N_features) for ranking. Columns are
    FEATURE_NAMES, in that order; FEATURES says why each one is there.
    """
    if not pois:
        return np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32)

    goal_text = " ".join(user_goals).lower().replace("_", " ")
    goal_embedding = embed_query(goal_text)
    poi_embeddings = embed_texts([p["description"] for p in pois])
    semantic = poi_embeddings @ goal_embedding
    semantic_rank = _percentile_ranks(semantic)

    # Which goal features this query can switch on at all. Words match at a
    # word start only, so "art" matches "arts" but not "party".
    wanted = {name: any(re.search(rf"\b{w}", goal_text) for w in words)
              for name, (words, _) in GOAL_RULES.items()}

    features = []
    for i, poi in enumerate(pois):
        category = poi.get("category", "attraction")
        tags = poi.get("tags") or {}

        matches = [1.0 if wanted[name] and category in cats else 0.0
                   for name, (_, cats) in GOAL_RULES.items()]

        features.append([
            semantic[i],
            semantic_rank[i],
            max(matches),
            *matches,
            1.0 if "wikipedia" in tags or "wikidata" in tags else 0.0,
            min(len(tags) / 20.0, 1.0),
            1.0 if any(k in tags for k in ("website", "contact:website", "opening_hours")) else 0.0,
            1.0 if "tourism" in tags else 0.0,
        ])

    return np.array(features, dtype=np.float32)


def extract_features_explained(user_goals: list[str], pois: list[dict]) -> tuple[np.ndarray, list[dict]]:
    """Extract features and return named feature dicts alongside the matrix."""
    matrix = extract_features(user_goals, pois)
    explanations = []
    for row in matrix:
        explanations.append(dict(zip(FEATURE_NAMES, row.tolist())))
    return matrix, explanations
