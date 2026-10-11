import numpy as np
import pytest

from backend.ml.ranker.features import FEATURE_NAMES, FEATURES, extract_features
from backend.ml.ranker.metrics import evaluate_scores, ndcg_at_k, precision_at_k
from backend.ml.ranker.model import POIRanker


SAMPLE_POIS = [
    {"name": "Ramen Ichiran", "category": "food", "description": "ramen food restaurant japanese noodles", "tags": {"amenity": "restaurant", "cuisine": "ramen"}},
    {"name": "Senso-ji Temple", "category": "history", "description": "senso-ji temple historic buddhist shrine tokyo", "tags": {"historic": "temple", "wikipedia": "en:Sensō-ji", "tourism": "attraction"}},
    {"name": "Shinjuku Gyoen", "category": "nature", "description": "shinjuku gyoen national park garden", "tags": {"leisure": "park"}},
    {"name": "Fabric Nightclub", "category": "nightlife", "description": "fabric nightclub music dancing bar", "tags": {"amenity": "nightclub"}},
    {"name": "Isetan", "category": "shopping", "description": "isetan shopping department store", "tags": {"shop": "department_store", "website": "https://example.com"}},
]


def column(features: np.ndarray, name: str) -> np.ndarray:
    return features[:, FEATURE_NAMES.index(name)]


def synthetic_dataset(n_groups: int, seed: int, group_size: int = 30):
    """Random feature rows whose label depends on columns 0 and 2, so there is something to learn."""
    rng = np.random.default_rng(seed)
    X = rng.random((n_groups * group_size, len(FEATURE_NAMES))).astype(np.float32)
    X[:, 2] = (X[:, 2] > 0.5).astype(np.float32)
    y = np.clip(np.round(4 * (0.6 * X[:, 0] + 0.4 * X[:, 2])), 0, 4).astype(np.float32)
    return X, y, [group_size] * n_groups


# ── Features ──────────────────────────────────────────────────────────────────

def test_feature_extraction_shape():
    goals = ["local food", "authentic cuisine"]
    features = extract_features(goals, SAMPLE_POIS)
    assert features.shape == (len(SAMPLE_POIS), len(FEATURE_NAMES))
    assert features.dtype == np.float32
    assert extract_features(goals, []).shape == (0, len(FEATURE_NAMES))


def test_every_feature_has_a_reason():
    assert FEATURE_NAMES[0] == "semantic_score"  # scorer fallback and the benchmark baseline read column 0
    assert all(reason for reason in FEATURES.values())


def test_feature_semantic_score_range():
    goals = ["food"]
    features = extract_features(goals, SAMPLE_POIS)
    semantic_scores = features[:, 0]
    # cosine similarity of normalized vectors is in [-1, 1]
    assert all(-1.01 <= s <= 1.01 for s in semantic_scores)
    ranks = column(features, "semantic_rank")
    assert ranks[np.argmax(semantic_scores)] == 1.0
    assert ranks[np.argmin(semantic_scores)] == 0.0


def test_food_goal_scores_food_poi_higher():
    goals = ["local food", "restaurants", "cuisine"]
    features = extract_features(goals, SAMPLE_POIS)
    food_score = features[0, 0]   # Ramen Ichiran
    nature_score = features[2, 0] # Shinjuku Gyoen
    assert food_score > nature_score


def test_goal_match_features_are_in_the_vector():
    # shopping_match and adventure_match used to be computed and then dropped
    features = extract_features(["shopping", "food"], SAMPLE_POIS)
    assert column(features, "shopping_match").tolist() == [0, 0, 0, 0, 1]
    assert column(features, "cuisine_match").tolist() == [1, 0, 0, 0, 0]
    assert column(features, "category_match").tolist() == [1, 0, 0, 0, 1]
    assert column(features, "nightlife_match").sum() == 0   # nightlife was not asked for

    attraction = [{"name": "Sky Tower", "category": "attraction", "description": "sky tower viewpoint",
                   "tags": {"tourism": "viewpoint"}}]
    assert column(extract_features(["hidden_gems"], attraction), "adventure_match")[0] == 1.0


def test_goal_words_match_whole_words_only():
    features = extract_features(["party"], SAMPLE_POIS)
    assert column(features, "culture_match").sum() == 0   # "art" is inside "party"
    assert column(features, "nightlife_match").tolist() == [0, 0, 0, 1, 0]


def test_tag_features():
    features = extract_features(["history"], SAMPLE_POIS)
    assert column(features, "is_notable").tolist() == [0, 1, 0, 0, 0]
    assert column(features, "is_tourism").tolist() == [0, 1, 0, 0, 0]
    assert column(features, "has_details").tolist() == [0, 0, 0, 0, 1]
    assert column(features, "tag_count_norm")[1] == pytest.approx(3 / 20)


# ── Metrics ───────────────────────────────────────────────────────────────────

def test_ndcg_perfect_and_worst_order():
    y = np.array([0, 4, 0, 3, 1])
    assert ndcg_at_k(y, y.astype(float), 3) == pytest.approx(1.0)
    assert ndcg_at_k(y, -y.astype(float), 3) < 0.1


def test_ndcg_ranks_the_whole_list_before_cutting_at_k():
    # The only relevant item sits at position 3. A scorer that puts it first
    # is perfect at k=2; one that puts it last finds nothing in its top 2.
    # (The old version looked only at the first k items and never saw it.)
    y = np.array([0, 0, 4])
    assert ndcg_at_k(y, np.array([0.1, 0.2, 0.9]), 2) == pytest.approx(1.0)
    assert ndcg_at_k(y, np.array([0.9, 0.2, 0.1]), 2) == 0.0


def test_ndcg_gives_no_credit_for_ties():
    y = np.array([4, 0, 0, 0, 0, 0])   # best item happens to be first
    assert ndcg_at_k(y, np.zeros(6), 1) == pytest.approx(1 / 6)


def test_ndcg_uses_exponential_gain():
    # swapping a 4 for a 3 at the top costs more than linear gain would say
    y = np.array([4, 3, 0])
    swapped = ndcg_at_k(y, np.array([2.0, 3.0, 1.0]), 3)
    dcg = 7 + 15 / np.log2(3)
    ideal = 15 + 7 / np.log2(3)
    assert swapped == pytest.approx(dcg / ideal)


def test_precision_at_k():
    y = np.array([4, 3, 2, 0])
    assert precision_at_k(y, np.array([4.0, 3.0, 2.0, 1.0]), 2) == 1.0
    assert precision_at_k(y, np.array([1.0, 2.0, 3.0, 4.0]), 2) == 0.0   # a label of 2 is not "relevant"


def test_evaluate_scores_averages_over_groups():
    y = np.array([4, 0, 0, 4], dtype=float)
    scores = np.array([1.0, 0.0, 1.0, 0.0])   # first group right, second group wrong
    out = evaluate_scores(y, scores, [2, 2], ks=[1])
    assert out["ndcg@1"] == pytest.approx(0.5)
    assert out["precision@1"] == pytest.approx(0.5)


# ── Model ─────────────────────────────────────────────────────────────────────

def test_ranker_learns_and_beats_random():
    X, y, groups = synthetic_dataset(20, seed=0)
    X_test, y_test, groups_test = synthetic_dataset(5, seed=1)
    ranker = POIRanker()
    ranker.train(X, y, groups)
    scores = ranker.score(X_test)
    assert len(scores) == len(X_test)
    # The old model could not split its 35 training rows and emitted 3 distinct scores
    assert len(set(np.round(scores, 6))) > 10
    model = evaluate_scores(y_test, scores, groups_test)["ndcg@10"]
    random = evaluate_scores(y_test, np.random.default_rng(0).random(len(y_test)), groups_test)["ndcg@10"]
    assert model > 0.9 > random


def test_ranker_rank_pois_and_explain():
    X, y, groups = synthetic_dataset(10, seed=0)
    ranker = POIRanker()
    ranker.train(X, y, groups)
    ranked = ranker.rank_pois(SAMPLE_POIS[:2], np.array([0.2, 0.9]))
    assert [p["name"] for p in ranked] == ["Senso-ji Temple", "Ramen Ichiran"]
    assert ranked[0]["relevance_score"] >= ranked[1]["relevance_score"]

    explanation = ranker.explain(X[:1])[0]
    assert set(explanation) == set(FEATURE_NAMES) | {"bias"}
    importance = ranker.feature_importance()
    assert max(importance, key=importance.get) in ("semantic_score", "category_match")


def test_save_is_atomic_and_loads_back(tmp_path):
    X, y, groups = synthetic_dataset(10, seed=0)
    ranker = POIRanker()
    ranker.train(X, y, groups)
    path = tmp_path / "ranker.pkl"
    ranker.save(path)
    assert [p.name for p in tmp_path.iterdir()] == ["ranker.pkl"]   # no temp file left behind
    assert np.allclose(POIRanker.load(path).score(X[:5]), ranker.score(X[:5]))


def test_untrained_ranker_refuses_to_score():
    with pytest.raises(RuntimeError):
        POIRanker().score(np.zeros((1, len(FEATURE_NAMES))))


# ── Training data and serving ─────────────────────────────────────────────────

def test_labelled_set_is_complete():
    from backend.ml.ranker.trainer import HOLDOUT_CITIES, load_labelled_queries
    queries = load_labelled_queries()
    # Whichever cities have a fixture and a label file; enough of them to
    # cross validate, with a held-out city for the gate and others to train on.
    cities = {q["city"] for q in queries}
    assert len(cities) >= 4 and len(queries) >= 16
    assert cities & set(HOLDOUT_CITIES) and cities - set(HOLDOUT_CITIES)
    for q in queries:
        assert len(q["pois"]) == len(q["labels"]) >= 50
        assert set(np.unique(q["labels"])) <= {0, 1, 2, 3, 4}
        assert q["labels"].max() >= 3   # every query has something worth finding


def test_rank_pois_falls_back_to_semantic_without_a_model(monkeypatch, tmp_path):
    from backend.ml.ranker import model, scorer
    monkeypatch.setattr(scorer, "_ranker", None)
    monkeypatch.setattr(model, "MODEL_PATH", tmp_path / "missing.pkl")
    pois = [{**p, "id": i} for i, p in enumerate(SAMPLE_POIS)]
    ranked, source = scorer.rank_pois(["food"], pois, top_k=3, with_source=True)
    assert source == "semantic"
    assert ranked[0]["name"] == "Ramen Ichiran"


def test_rank_pois_uses_the_model(monkeypatch):
    from backend.ml.ranker import scorer
    X, y, groups = synthetic_dataset(10, seed=0)
    ranker = POIRanker()
    ranker.train(X, y, groups)
    monkeypatch.setattr(scorer, "_ranker", ranker)
    pois = [{**p, "id": i} for i, p in enumerate(SAMPLE_POIS)]
    ranked, source = scorer.rank_pois(["food"], pois, top_k=3, explain=True, with_source=True)
    assert source == "model"
    assert len(ranked) == 3
    assert "contributions" in ranked[0]["explanation"]
