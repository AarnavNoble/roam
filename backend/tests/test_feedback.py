"""
Tests for the feedback store and incremental retraining pipeline.
Uses a temporary DB path to avoid touching production data.
"""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch


# ── Feedback store tests ──────────────────────────────────────────────────────

def make_store(tmp_path):
    """Return feedback_store module wired to a temp DB."""
    import importlib
    import backend.ml.ranker.feedback_store as fs
    # Patch the DB path to an isolated temp file
    fs.DB_PATH = tmp_path / "test_feedback.db"
    return fs


def test_log_and_count(tmp_path):
    fs = make_store(tmp_path)
    assert fs.get_feedback_count() == 0
    fs.log_feedback(poi_id=1, relevant=True, poi_name="Eiffel Tower", category="attraction", goals=["history"])
    assert fs.get_feedback_count() == 1


def test_multiple_signals(tmp_path):
    fs = make_store(tmp_path)
    for i in range(5):
        fs.log_feedback(poi_id=i, relevant=(i % 2 == 0))
    assert fs.get_feedback_count() == 5


def test_get_feedback_as_training_data(tmp_path):
    fs = make_store(tmp_path)
    fs.log_feedback(1, True, "Louvre", "culture", ["history", "culture"])
    fs.log_feedback(2, False, "McDonald's", "food", ["food"])
    rows = fs.get_feedback_as_training_data()
    assert len(rows) == 2
    assert rows[0]["poi_id"] == 1
    assert rows[0]["relevant"] is True
    assert "history" in rows[0]["goals"]


def test_goals_stored_and_retrieved(tmp_path):
    fs = make_store(tmp_path)
    fs.log_feedback(10, True, "Park", "nature", ["nature", "adventure"])
    rows = fs.get_feedback_as_training_data()
    assert set(rows[0]["goals"]) == {"nature", "adventure"}


def test_empty_goals_ok(tmp_path):
    fs = make_store(tmp_path)
    fs.log_feedback(99, False)  # no optional args
    rows = fs.get_feedback_as_training_data()
    assert len(rows) == 1
    assert rows[0]["goals"] == [""]  # empty string splits to [""]


# ── Retrain tests ─────────────────────────────────────────────────────────────

def test_retrain_not_triggered_below_threshold(tmp_path):
    import backend.ml.ranker.retrain as rt
    import backend.ml.ranker.feedback_store as fs
    fs.DB_PATH = tmp_path / "retrain_test.db"
    rt._feedback_count_at_last_train = 0

    # Log fewer than threshold signals
    for i in range(rt.RETRAIN_THRESHOLD - 1):
        fs.log_feedback(i, True, f"Place{i}", "culture", ["culture"])

    result = rt.retrain_if_needed()
    assert result is False


def test_retrain_triggered_at_threshold(tmp_path):
    import backend.ml.ranker.retrain as rt
    import backend.ml.ranker.feedback_store as fs
    fs.DB_PATH = tmp_path / "retrain_thresh.db"
    rt._feedback_count_at_last_train = 0

    # Log enough signals — need ≥2 per goal group for LambdaRank
    for i in range(rt.RETRAIN_THRESHOLD):
        fs.log_feedback(i, i % 2 == 0, f"Place{i}", "culture", ["history"])

    result = rt.retrain_if_needed()
    assert result is True


def test_build_feedback_training_data_shape(tmp_path):
    import backend.ml.ranker.retrain as rt
    import backend.ml.ranker.feedback_store as fs
    fs.DB_PATH = tmp_path / "shape_test.db"

    # Add 4 signals with same goals so they form one group
    for i in range(4):
        fs.log_feedback(i, i < 2, f"Place{i}", "food", ["food"])

    result = rt.build_feedback_training_data()
    assert result is not None
    X, y, groups = result
    assert X.shape[0] == 4
    assert y.shape[0] == 4
    assert sum(groups) == 4


def test_build_feedback_returns_none_when_empty(tmp_path):
    import backend.ml.ranker.retrain as rt
    import backend.ml.ranker.feedback_store as fs
    fs.DB_PATH = tmp_path / "empty_test.db"

    result = rt.build_feedback_training_data()
    assert result is None
