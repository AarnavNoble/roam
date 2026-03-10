"""
Learning-to-rank model for POI scoring.
Uses LightGBM with a lambdarank objective — industry standard for ranking tasks.
"""

import lightgbm as lgb
import numpy as np
import pickle
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parents[3] / "data" / "ranker_model.pkl"


class POIRanker:
    def __init__(self):
        self.model = lgb.LGBMRanker(
            objective="lambdarank",
            metric="ndcg",
            ndcg_eval_at=[5, 10],
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            feature_fraction=0.8,
            bagging_fraction=0.8,
            bagging_freq=5,
            verbose=-1,
        )
        self.is_trained = False

    def train(self, X: np.ndarray, y: np.ndarray, group: list[int]) -> None:
        """
        Train on feature matrix X, relevance labels y, query groups.
        group: list of ints, each = number of POIs per query (sums to len(X))
        """
        self.model.fit(
            X, y,
            group=group,
            eval_set=[(X, y)],
            eval_group=[group],
        )
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

    def save(self) -> None:
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(self, f)
        print(f"Model saved to {MODEL_PATH}")

    @classmethod
    def load(cls) -> "POIRanker":
        if not MODEL_PATH.exists():
            raise FileNotFoundError("Ranker model not found. Run trainer.py first.")
        with open(MODEL_PATH, "rb") as f:
            return pickle.load(f)
