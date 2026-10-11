"""
Tests for the feedback store and for turning feedback into training rows.
conftest gives every test an empty database, so nothing here touches real data.
The retraining gate itself is covered in test_registry.py.
"""

import backend.ml.ranker.feedback_store as fs


# ── Feedback store tests ──────────────────────────────────────────────────────

def test_log_and_count():
    assert fs.get_feedback_count() == 0
    fs.log_feedback(poi_id=1, relevant=True, poi_name="Eiffel Tower", category="attraction", goals=["history"])
    assert fs.get_feedback_count() == 1


def test_multiple_signals():
    for i in range(5):
        fs.log_feedback(poi_id=i, relevant=(i % 2 == 0))
    assert fs.get_feedback_count() == 5


def test_get_feedback_as_training_data():
    fs.log_feedback(1, True, "Louvre", "culture", ["history", "culture"])
    fs.log_feedback(2, False, "McDonald's", "food", ["food"])
    rows = fs.get_feedback_as_training_data()
    assert len(rows) == 2
    assert rows[0]["poi_id"] == 1
    assert rows[0]["relevant"] is True
    assert "history" in rows[0]["goals"]
    assert rows[1]["relevant"] is False


def test_goals_stored_and_retrieved():
    fs.log_feedback(10, True, "Park", "nature", ["nature", "adventure"])
    rows = fs.get_feedback_as_training_data()
    assert set(rows[0]["goals"]) == {"nature", "adventure"}


def test_empty_goals_ok():
    fs.log_feedback(99, False)  # no optional args
    rows = fs.get_feedback_as_training_data()
    assert len(rows) == 1
    assert rows[0]["goals"] == []


def test_large_osm_id_survives():
    fs.log_feedback(12_345_678_901, True, "Somewhere", "food", ["food"])  # way ids exceed 32 bits
    assert fs.get_feedback_as_training_data()[0]["poi_id"] == 12_345_678_901


# ── Feedback as training data ─────────────────────────────────────────────────

def test_build_feedback_training_data_shape():
    import backend.ml.ranker.retrain as rt
    from backend.ml.ranker.features import FEATURE_NAMES

    # Add 4 signals with same goals so they form one group
    for i in range(4):
        fs.log_feedback(i, i < 2, f"Place{i}", "food", ["food"])

    result = rt.build_feedback_training_data()
    assert result is not None
    X, y, groups = result
    assert X.shape == (4, len(FEATURE_NAMES))
    assert y.tolist() == [3, 3, 0, 0]   # thumbs up is a 3, thumbs down a 0
    assert groups == [4]


def test_build_feedback_groups_by_goals_and_drops_singletons():
    import backend.ml.ranker.retrain as rt

    fs.log_feedback(1, True, "A", "food", ["food", "history"])
    fs.log_feedback(2, False, "B", "history", ["history", "food"])   # same goals, other order
    fs.log_feedback(3, True, "C", "nature", ["nature"])              # alone in its group

    X, y, groups = rt.build_feedback_training_data()
    assert groups == [2]
    assert len(X) == len(y) == 2


def test_build_feedback_returns_none_when_empty():
    import backend.ml.ranker.retrain as rt

    result = rt.build_feedback_training_data()
    assert result is None


def test_retrain_not_triggered_below_threshold():
    import backend.ml.ranker.retrain as rt
    from backend.ml.ranker import registry

    # Log fewer than threshold signals
    for i in range(rt.RETRAIN_THRESHOLD - 1):
        fs.log_feedback(i, True, f"Place{i}", "culture", ["culture"])

    assert rt.retrain_if_needed() == "skipped"
    assert registry.history() == []
