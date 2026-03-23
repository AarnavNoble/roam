"""
Feature extraction for POI ranking.
Combines semantic embeddings with metadata features.
"""

import numpy as np
from sklearn.preprocessing import LabelEncoder
from backend.ml.rag.embedder import embed_texts, embed_query

CATEGORIES = ["food", "nature", "history", "culture", "nightlife", "shopping", "adventure", "attraction"]

FEATURE_NAMES = [
    "semantic_score", "category_match", "name_length_norm",
    "has_description", "cuisine_match", "nature_match",
    "history_match", "nightlife_match",
]
_label_enc = LabelEncoder().fit(CATEGORIES)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two normalized vectors."""
    return float(np.dot(a, b))


def extract_features(user_goals: list[str], pois: list[dict]) -> np.ndarray:
    """
    Build feature matrix (N_pois, N_features) for ranking.

    Features per POI:
    - [0]   semantic_score: cosine sim between goal embedding and POI description embedding
    - [1]   category_match: fraction of user goals that match POI category keywords
    - [2]   name_length_norm: normalized name length (proxy for specificity)
    - [3]   has_description: 1 if POI has extra OSM tags beyond name
    - [4]   cuisine_match: 1 if user mentioned food/cuisine and POI is food
    - [5]   nature_match: 1 if user mentioned nature and POI is nature
    - [6]   history_match: 1 if user mentioned history/culture and POI is history/culture
    - [7]   nightlife_match: 1 if user mentioned nightlife and POI is nightlife
    """
    goal_text = " ".join(user_goals).lower()
    goal_embedding = embed_query(goal_text)

    poi_descriptions = [p["description"] for p in pois]
    poi_embeddings = embed_texts(poi_descriptions)

    features = []
    for i, poi in enumerate(pois):
        poi_emb = poi_embeddings[i]
        category = poi.get("category", "attraction")

        semantic_score = cosine_similarity(goal_embedding, poi_emb)
        name_length_norm = min(len(poi["name"]) / 50.0, 1.0)
        has_description = 1.0 if len(poi.get("tags", {})) > 2 else 0.0

        cuisine_match = 1.0 if any(w in goal_text for w in ("food", "eat", "restaurant", "cuisine", "dinner", "lunch")) and category == "food" else 0.0
        nature_match = 1.0 if any(w in goal_text for w in ("nature", "park", "outdoor", "hiking", "beach")) and category == "nature" else 0.0
        history_match = 1.0 if any(w in goal_text for w in ("history", "historic", "museum", "culture", "art")) and category in ("history", "culture") else 0.0
        nightlife_match = 1.0 if any(w in goal_text for w in ("nightlife", "bar", "club", "party", "drinks")) and category == "nightlife" else 0.0
        category_match = (cuisine_match + nature_match + history_match + nightlife_match)

        features.append([
            semantic_score,
            category_match,
            name_length_norm,
            has_description,
            cuisine_match,
            nature_match,
            history_match,
            nightlife_match,
        ])

    return np.array(features, dtype=np.float32)


def extract_features_explained(user_goals: list[str], pois: list[dict]) -> tuple[np.ndarray, list[dict]]:
    """Extract features and return named feature dicts alongside the matrix."""
    matrix = extract_features(user_goals, pois)
    explanations = []
    for row in matrix:
        explanations.append(dict(zip(FEATURE_NAMES, row.tolist())))
    return matrix, explanations
