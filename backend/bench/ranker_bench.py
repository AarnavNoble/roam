"""
Ranker benchmark: leave-one-scenario-out cross-validation with baselines.

Absolute NDCG on this dataset is close to meaningless — the relevance labels in
trainer.TRAINING_SCENARIOS are hand-assigned alongside the category features the
model reads, so a model fit on all of them scores near the ceiling by
construction. The number worth quoting is the *lift over a baseline that uses
only semantic similarity*, measured on scenarios the model never saw.

Baselines:
  random        shuffled order — the floor
  cosine_only   rank by feature[0] (goal/POI embedding cosine) alone, no model
  lambdarank    the full 8-feature LightGBM model

Folds are whole scenarios, never rows: POIs inside one scenario are labelled
relative to each other, so splitting rows would leak.

Run: python -m backend.bench.ranker_bench
"""

import json
from pathlib import Path

import numpy as np
from sklearn.metrics import ndcg_score

from backend.ml.ranker.features import extract_features
from backend.ml.ranker.model import POIRanker
from backend.ml.ranker.trainer import TRAINING_SCENARIOS

RESULTS_PATH = Path(__file__).resolve().parent / "results_ranker.json"
KS = [3, 5]
RELEVANT_THRESHOLD = 2.0
N_RANDOM_TRIALS = 200
SEED = 0


def ndcg_at_k(y_true: np.ndarray, y_score: np.ndarray, k: int) -> float:
    """
    NDCG@k over the full candidate list.

    Note this differs from metrics.ndcg_at_k, which slices the first k entries
    in their original order before scoring — that measures something else.
    Here the whole list is passed and sklearn truncates the *ranked* result.
    """
    if len(y_true) < 2:
        return 1.0
    return float(ndcg_score(y_true[None, :], y_score[None, :], k=k))


def precision_at_k(y_true: np.ndarray, y_score: np.ndarray, k: int) -> float:
    """Fraction of the top-k predicted items whose true relevance is >= threshold."""
    top_k = min(k, len(y_score))
    top_idx = np.argsort(y_score)[::-1][:top_k]
    return float(sum(1 for i in top_idx if y_true[i] >= RELEVANT_THRESHOLD) / top_k)


def build_scenario_data() -> list[dict]:
    """Extract features once per scenario. Returns [{X, y, goals, n}]."""
    data = []
    for scenario in TRAINING_SCENARIOS:
        pois = scenario["pois"]
        for poi in pois:
            poi.setdefault("tags", {})
        X = extract_features(scenario["goals"], pois)
        y = np.array(scenario["relevance"], dtype=np.float32)
        data.append({"X": X, "y": y, "goals": scenario["goals"], "n": len(pois)})
    return data


def score_random(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.permutation(n).astype(np.float64)


def evaluate_fold(y: np.ndarray, scores: np.ndarray) -> dict:
    out = {}
    for k in KS:
        out[f"ndcg@{k}"] = ndcg_at_k(y, scores, k)
        out[f"precision@{k}"] = precision_at_k(y, scores, k)
    return out


def mean_metrics(folds: list[dict]) -> dict:
    keys = folds[0].keys()
    return {k: float(np.mean([f[k] for f in folds])) for k in keys}


def run() -> dict:
    data = build_scenario_data()
    n_scenarios = len(data)
    rng = np.random.default_rng(SEED)

    results = {"lambdarank": [], "cosine_only": [], "random": []}

    for held_out in range(n_scenarios):
        train_idx = [i for i in range(n_scenarios) if i != held_out]
        test = data[held_out]

        X_train = np.vstack([data[i]["X"] for i in train_idx])
        y_train = np.concatenate([data[i]["y"] for i in train_idx])
        groups_train = [data[i]["n"] for i in train_idx]

        ranker = POIRanker()
        ranker.train(X_train, y_train, groups_train)
        model_scores = ranker.score(test["X"])
        results["lambdarank"].append(evaluate_fold(test["y"], model_scores))

        # Baseline: semantic similarity alone, feature column 0
        results["cosine_only"].append(evaluate_fold(test["y"], test["X"][:, 0].astype(np.float64)))

        # Baseline: random, averaged over many shuffles to reduce variance
        trials = [evaluate_fold(test["y"], score_random(test["n"], rng))
                  for _ in range(N_RANDOM_TRIALS)]
        results["random"].append(mean_metrics(trials))

    return {name: mean_metrics(folds) for name, folds in results.items()}


def report(summary: dict) -> None:
    print("\n" + "=" * 72)
    print(f"RANKER — leave-one-scenario-out CV ({len(TRAINING_SCENARIOS)} folds, "
          f"{sum(len(s['pois']) for s in TRAINING_SCENARIOS)} POIs total)")
    print("=" * 72)

    metric_names = [f"ndcg@{k}" for k in KS] + [f"precision@{k}" for k in KS]
    header = f"{'model':<14}" + "".join(f"{m:>14}" for m in metric_names)
    print(header)
    print("-" * 72)
    for name in ("random", "cosine_only", "lambdarank"):
        row = f"{name:<14}" + "".join(f"{summary[name][m]:>14.3f}" for m in metric_names)
        print(row)

    print("-" * 72)
    for m in metric_names:
        base = summary["cosine_only"][m]
        model = summary["lambdarank"][m]
        lift = (model - base) / base * 100 if base else float("nan")
        print(f"lambdarank vs cosine_only, {m}: {lift:+.1f}%")


def main():
    summary = run()
    report(summary)
    RESULTS_PATH.write_text(json.dumps(summary, indent=2))
    print(f"\nResults written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
