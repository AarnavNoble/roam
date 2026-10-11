"""
Trains the POI ranker from the committed labelled set.

Training data is real OpenStreetMap candidate pools for city neighbourhoods
(bench/fixtures/*.json), each judged against several goal sets on a 0 to 4
relevance scale (bench/labels/*.json, see bench/label_pois.py for how and for
the caveat that the labels come from an LLM judge). Both live in the
repository, so training needs no network and gives the same model every time.
The Dockerfile runs this at image build. Every city that has a label file is
used, so adding a city is: fetch its fixture, label it, rerun this.

The cities in HOLDOUT_CITIES are held out and never trained on, here or in
retraining. They are the fixed yardstick the promotion gate (retrain.py) uses
to compare a retrained candidate with the serving model: a comparison only
means something on data neither model has seen. The price is that the shipped
model learns from fewer cities. The split is by city, never by row, because
all the queries of one city share the same POIs.

The benchmark (bench/ranker_bench.py) does not use this split. It cross
validates over every labelled city to get an error bar.

Run: python -m backend.ml.ranker.trainer
"""

import json
from pathlib import Path

import numpy as np

from .features import extract_features  # before model: see the note in model.py
from .metrics import evaluate_ranker, evaluate_scores
from .model import POIRanker

BENCH_DIR = Path(__file__).resolve().parents[2] / "bench"
FIXTURE_DIR = BENCH_DIR / "fixtures"
LABEL_DIR = BENCH_DIR / "labels"

# Picked once, before any model was evaluated, and not changed since.
HOLDOUT_CITIES = ("bangkok_sukhumvit", "london_soho")


def load_labelled_queries() -> list[dict]:
    """
    Join fixtures with labels. Returns one dict per query group:
    {"city", "goals", "pois", "labels"} with labels aligned to pois.
    """
    queries = []
    for label_path in sorted(LABEL_DIR.glob("*.json")):
        labelled = json.loads(label_path.read_text())
        fixture = json.loads((FIXTURE_DIR / label_path.name).read_text())
        for q in labelled["queries"]:
            pois = [p for p in fixture["pois"] if str(p["id"]) in q["labels"]]
            if not pois:
                continue
            queries.append({
                "city": labelled["fixture"],
                "goals": q["goals"],
                "pois": pois,
                "labels": np.array([q["labels"][str(p["id"])] for p in pois], dtype=np.float32),
            })
    if not queries:
        raise FileNotFoundError(f"No labelled queries in {LABEL_DIR}. Run: python -m backend.bench.label_pois")
    return queries


def build_dataset(queries: list[dict]) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Turn query dicts into (X, y, groups) for LightGBM: rows grouped by query."""
    X = np.vstack([extract_features(q["goals"], q["pois"]) for q in queries])
    y = np.concatenate([q["labels"] for q in queries])
    groups = [len(q["pois"]) for q in queries]
    return X, y, groups


def load_base_data() -> tuple[tuple, tuple]:
    """Return (train, holdout), each an (X, y, groups) triple, split by city."""
    queries = load_labelled_queries()
    train = [q for q in queries if q["city"] not in HOLDOUT_CITIES]
    holdout = [q for q in queries if q["city"] in HOLDOUT_CITIES]
    if not train or not holdout:
        raise ValueError("Need labelled cities on both sides of the split; "
                         f"none of {HOLDOUT_CITIES} is labelled" if train else "only held-out cities are labelled")
    return build_dataset(train), build_dataset(holdout)


def train() -> POIRanker:
    (X, y, groups), (X_hold, y_hold, groups_hold) = load_base_data()
    print(f"Training on {len(X)} labelled (goals, POI) pairs in {len(groups)} queries; "
          f"holding out {len(X_hold)} pairs in {len(groups_hold)} queries")

    ranker = POIRanker()
    ranker.train(X, y, groups)
    ranker.save()

    model = evaluate_ranker(ranker, X_hold, y_hold, groups_hold)
    cosine = evaluate_scores(y_hold, X_hold[:, 0], groups_hold)
    print(f"Held-out NDCG@10: model {model['ndcg@10']:.3f}, cosine only {cosine['ndcg@10']:.3f}")
    print("Ranker training complete.")
    return ranker


if __name__ == "__main__":
    train()
