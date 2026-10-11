"""
Tests for the promotion gate: a retrained model replaces the serving model only
if it holds up on held-out data, and every run is recorded either way.

The base and held-out data are small synthetic sets (patched in for
trainer.load_base_data) so the tests are fast and the outcome is certain: the
label is a known function of two features, "good" feedback follows that
function and "bad" feedback inverts it.
"""

import threading

import numpy as np
import pytest

import backend.ml.ranker.feedback_store as fs
import backend.ml.ranker.retrain as rt
from backend.ml.ranker import model, registry, scorer
from backend.ml.ranker.features import FEATURE_NAMES
from backend.ml.ranker.model import POIRanker


def synthetic_dataset(n_groups: int, seed: int, inverted: bool = False, group_size: int = 30):
    rng = np.random.default_rng(seed)
    X = rng.random((n_groups * group_size, len(FEATURE_NAMES))).astype(np.float32)
    X[:, 2] = (X[:, 2] > 0.5).astype(np.float32)
    y = np.clip(np.round(4 * (0.6 * X[:, 0] + 0.4 * X[:, 2])), 0, 4).astype(np.float32)
    if inverted:
        y = 4 - y
    return X, y, [group_size] * n_groups


def log_feedback_rows(n: int) -> None:
    for i in range(n):
        fs.log_feedback(i, i % 2 == 0, f"Place{i}", "food", ["food"])


@pytest.fixture
def incumbent(monkeypatch, tmp_path):
    """A serving model trained on the synthetic base set, with the model file in a temp dir."""
    train, holdout = synthetic_dataset(20, seed=0), synthetic_dataset(6, seed=1)
    monkeypatch.setattr(rt, "load_base_data", lambda: (train, holdout))
    monkeypatch.setattr(model, "MODEL_PATH", tmp_path / "ranker_model.pkl")
    ranker = POIRanker()
    ranker.train(*train)
    monkeypatch.setattr(scorer, "_ranker", ranker)
    return ranker


# ── The rule ──────────────────────────────────────────────────────────────────

def test_decide_promotes_within_tolerance_and_rejects_beyond_it():
    incumbent_metrics = {"ndcg@10": 0.80}
    assert registry.decide({"ndcg@10": 0.85}, incumbent_metrics)[0] == "promoted"
    assert registry.decide({"ndcg@10": 0.80 - registry.TOLERANCE / 2}, incumbent_metrics)[0] == "promoted"
    status, reason = registry.decide({"ndcg@10": 0.80 - registry.TOLERANCE * 2}, incumbent_metrics)
    assert status == "rejected"
    assert "regression" in reason


def test_decide_promotes_when_there_is_no_incumbent():
    status, reason = registry.decide({"ndcg@10": 0.5}, {})
    assert status == "promoted"
    assert "no usable incumbent" in reason


# ── The gate, end to end ──────────────────────────────────────────────────────

def test_bad_candidate_is_rejected_and_serving_model_untouched(incumbent, monkeypatch):
    # Feedback that says the opposite of the truth, and a lot of it
    monkeypatch.setattr(rt, "build_feedback_training_data",
                        lambda: synthetic_dataset(60, seed=2, inverted=True))
    log_feedback_rows(rt.RETRAIN_THRESHOLD)

    assert rt.retrain_if_needed() == "rejected"

    assert scorer.get_ranker() is incumbent          # still serving the old model
    assert not model.MODEL_PATH.exists()             # nothing was written to disk
    [run] = registry.history()
    assert run["status"] == "rejected"
    assert run["feedback_count"] == rt.RETRAIN_THRESHOLD
    assert run["metrics"]["ndcg@10"] < run["incumbent_metrics"]["ndcg@10"] - registry.TOLERANCE
    assert "regression" in run["reason"]


def test_good_candidate_is_promoted(incumbent, monkeypatch):
    monkeypatch.setattr(rt, "build_feedback_training_data", lambda: synthetic_dataset(20, seed=3))
    log_feedback_rows(rt.RETRAIN_THRESHOLD)

    assert rt.retrain_if_needed() == "promoted"

    serving = scorer.get_ranker()
    assert serving is not incumbent
    X, _, _ = synthetic_dataset(1, seed=4)
    assert np.allclose(POIRanker.load().score(X), serving.score(X))   # disk and memory agree
    assert list(model.MODEL_PATH.parent.iterdir()) == [model.MODEL_PATH]   # no temp file left
    [run] = registry.history()
    assert run["status"] == "promoted"
    assert run["metrics"]["ndcg@10"] >= run["incumbent_metrics"]["ndcg@10"] - registry.TOLERANCE


def test_first_model_is_promoted_when_nothing_is_serving(incumbent, monkeypatch):
    monkeypatch.setattr(scorer, "_ranker", None)     # and no model file on disk
    log_feedback_rows(rt.RETRAIN_THRESHOLD)

    assert rt.retrain_if_needed() == "promoted"
    assert model.MODEL_PATH.exists()
    [run] = registry.history()
    assert run["incumbent_metrics"] == {}
    assert "no usable incumbent" in run["reason"]


# ── Scheduling ────────────────────────────────────────────────────────────────

def test_next_run_waits_for_threshold_new_rows_since_last_run(incumbent):
    log_feedback_rows(rt.RETRAIN_THRESHOLD)
    assert rt.retrain_if_needed() != "skipped"
    assert registry.last_feedback_count() == rt.RETRAIN_THRESHOLD

    # The count comes from the table, so a "restart" (nothing kept in memory) changes nothing
    log_feedback_rows(rt.RETRAIN_THRESHOLD - 1)
    assert rt.retrain_if_needed() == "skipped"
    assert len(registry.history()) == 1

    log_feedback_rows(1)
    assert rt.retrain_if_needed() != "skipped"
    assert [r["feedback_count"] for r in registry.history()] == [rt.RETRAIN_THRESHOLD, 2 * rt.RETRAIN_THRESHOLD]


def test_rejected_run_also_resets_the_counter(incumbent, monkeypatch):
    monkeypatch.setattr(rt, "build_feedback_training_data",
                        lambda: synthetic_dataset(60, seed=2, inverted=True))
    log_feedback_rows(rt.RETRAIN_THRESHOLD)
    assert rt.retrain_if_needed() == "rejected"
    assert rt.retrain_if_needed() == "skipped"       # not retried on every request
    assert len(registry.history()) == 1


def test_training_failure_is_recorded_not_raised(incumbent, monkeypatch):
    def broken():
        raise ValueError("bad feedback row")
    monkeypatch.setattr(rt, "build_feedback_training_data", broken)
    log_feedback_rows(rt.RETRAIN_THRESHOLD)

    assert rt.retrain_if_needed() == "rejected"
    assert scorer.get_ranker() is incumbent
    [run] = registry.history()
    assert run["status"] == "rejected"
    assert "training failed" in run["reason"]


def test_concurrent_requests_retrain_once(incumbent, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_train(feedback_count):
        calls.append(feedback_count)
        started.set()
        release.wait(timeout=5)
        registry.record("promoted", "test", feedback_count)
        return "promoted"

    monkeypatch.setattr(rt, "_train_and_gate", slow_train)
    log_feedback_rows(rt.RETRAIN_THRESHOLD)

    results = []
    first = threading.Thread(target=lambda: results.append(rt.retrain_if_needed()))
    first.start()
    assert started.wait(timeout=5)
    assert rt.retrain_if_needed() == "skipped"       # second request, while the first is training
    release.set()
    first.join(timeout=5)

    assert results == ["promoted"]
    assert len(calls) == 1
    assert rt.retrain_if_needed() == "skipped"       # lock released, and nothing new to train on


def test_history_is_oldest_first_with_the_fields_the_api_serves():
    registry.record("promoted", "first", 10, {"ndcg@10": 0.7}, {})
    registry.record("rejected", "second", 20, {"ndcg@10": 0.5}, {"ndcg@10": 0.7})
    runs = registry.history()
    assert [r["reason"] for r in runs] == ["first", "second"]
    assert set(runs[0]) == {"id", "status", "reason", "feedback_count", "metrics",
                            "incumbent_metrics", "created_at"}
    assert registry.last_feedback_count() == 20
