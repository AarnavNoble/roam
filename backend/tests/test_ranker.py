import numpy as np
from backend.ml.ranker.features import extract_features
from backend.ml.ranker.model import POIRanker
from backend.ml.ranker.trainer import generate_training_data


SAMPLE_POIS = [
    {"name": "Ramen Ichiran", "category": "food", "description": "ramen food restaurant japanese noodles", "tags": {"amenity": "restaurant", "cuisine": "ramen"}},
    {"name": "Senso-ji Temple", "category": "history", "description": "senso-ji temple historic buddhist shrine tokyo", "tags": {"historic": "temple"}},
    {"name": "Shinjuku Gyoen", "category": "nature", "description": "shinjuku gyoen national park garden", "tags": {"leisure": "park"}},
    {"name": "Fabric Nightclub", "category": "nightlife", "description": "fabric nightclub music dancing bar", "tags": {"amenity": "nightclub"}},
]


def test_feature_extraction_shape():
    goals = ["local food", "authentic cuisine"]
    features = extract_features(goals, SAMPLE_POIS)
    assert features.shape == (len(SAMPLE_POIS), 8)
    assert features.dtype == np.float32


def test_feature_semantic_score_range():
    goals = ["food"]
    features = extract_features(goals, SAMPLE_POIS)
    semantic_scores = features[:, 0]
    # cosine similarity of normalized vectors is in [-1, 1]
    assert all(-1.0 <= s <= 1.0 for s in semantic_scores)


def test_food_goal_scores_food_poi_higher():
    goals = ["local food", "restaurants", "cuisine"]
    features = extract_features(goals, SAMPLE_POIS)
    food_score = features[0, 0]   # Ramen Ichiran
    nature_score = features[2, 0] # Shinjuku Gyoen
    assert food_score > nature_score


def test_ranker_train_and_score():
    X, y, groups = generate_training_data()
    ranker = POIRanker()
    ranker.train(X, y, groups)
    scores = ranker.score(X[:5])
    assert len(scores) == 5
    assert all(isinstance(s, float) for s in scores)


def test_ranker_rank_pois():
    X, y, groups = generate_training_data()
    ranker = POIRanker()
    ranker.train(X, y, groups)
    scores = ranker.score(np.array([[0.9, 1.0, 0.5, 1.0, 1.0, 0.0, 0.0, 0.0],
                                     [0.2, 0.0, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0]],
                                    dtype=np.float32))
    ranked = ranker.rank_pois(SAMPLE_POIS[:2], scores)
    assert ranked[0]["relevance_score"] >= ranked[1]["relevance_score"]
