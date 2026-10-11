"""
Learning-to-rank model for POI scoring: LightGBM with a lambdarank objective.

Why these parameters. The training set is a few thousand (goals, POI) rows in
a couple of dozen query groups (see trainer.py). That is small for gradient boosting,
so the trees are kept shallow and each leaf has to cover a real number of rows:

  num_leaves=15, min_child_samples=20   a leaf needs 20 rows behind it. (The
      previous training set had 35 rows in total, so with this same default no
      split was ever legal and the "model" was a constant plus one stump.)
  n_estimators=150, learning_rate=0.05  a modest number of small steps
  colsample_bytree=0.8                  each tree sees 80% of the features so
      no single feature carries the whole model
  lambdarank_truncation_level=20        optimise the order of the top 20, which
      is roughly how many stops an itinerary takes

These were fixed before running the benchmark and have not been tuned against
it: with a handful of cities there is no data to spare for a tuning split, and tuning
on the test folds would make the reported lift meaningless.
"""

# features must be imported before lightgbm: it loads torch, and on macOS
# lightgbm cannot find libomp unless torch has been loaded first.
from .features import FEATURE_NAMES  # isort: skip

import os
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np

MODEL_PATH = Path(__file__).resolve().parents[3] / "data" / "ranker_model.pkl"


class POIRanker:
    def __init__(self):
        self.model = lgb.LGBMRanker(
            objective="lambdarank",
            n_estimators=150,
            learning_rate=0.05,
            num_leaves=15,
            min_child_samples=20,
            colsample_bytree=0.8,
            lambdarank_truncation_level=20,
            importance_type="gain",
            random_state=0,
            n_jobs=1,          # with a fixed seed, one thread makes training reproducible
            verbose=-1,
        )
        self.is_trained = False

    def train(self, X: np.ndarray, y: np.ndarray, group: list[int]) -> None:
        """
        Train on feature matrix X, relevance labels y (integers 0 to 4), query groups.
        group: list of ints, each = number of POIs per query (sums to len(X))
        """
        self.model.fit(X, np.asarray(y).astype(int), group=group)
        self.is_trained = True

    def score(self, X: np.ndarray) -> np.ndarray:
        """Return relevance scores for each row in X."""
        if not self.is_trained:
            raise RuntimeError("Model not trained. Run trainer.py first.")
        return self.model.predict(X)

    def rank_pois(self, pois: list[dict], scores: np.ndarray) -> list[dict]:
        """Return POIs sorted by score descending."""
        ranked = sorted(zip(pois, scores), key=lambda x: x[1], reverse=True)
        result = []
        for poi, score in ranked:
            poi = poi.copy()
            poi["relevance_score"] = float(score)
            result.append(poi)
        return result

    def explain(self, X: np.ndarray) -> list[dict]:
        """Per-sample feature contributions using LightGBM's built-in SHAP values."""
        contribs = self.model.predict(X, pred_contrib=True)
        results = []
        for i in range(len(X)):
            row = {name: float(contribs[i][j]) for j, name in enumerate(FEATURE_NAMES)}
            row["bias"] = float(contribs[i][-1])
            results.append(row)
        return results

    def feature_importance(self) -> dict[str, float]:
        """Global feature importance from the trained model: total split gain per feature."""
        importances = self.model.feature_importances_
        return dict(zip(FEATURE_NAMES, [float(v) for v in importances]))

    def save(self, path: Path | None = None) -> None:
        """
        Write the model to disk atomically: pickle to a temp file next to the
        target, then os.replace. A reader (or a crash) never sees a half-written
        file, only the old model or the new one.
        """
        path = Path(path or MODEL_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp{os.getpid()}")
        with open(tmp, "wb") as f:
            pickle.dump(self, f)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path | None = None) -> "POIRanker":
        path = Path(path or MODEL_PATH)
        if not path.exists():
            raise FileNotFoundError("Ranker model not found. Run trainer.py first.")
        with open(path, "rb") as f:
            return pickle.load(f)
