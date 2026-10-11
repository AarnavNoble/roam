"""
Ranker benchmark: leave-one-city-out cross validation against two baselines.

The question it answers: on a city the model has never seen, does the LightGBM
ranker order candidates better than sorting by embedding similarity alone?
Cosine similarity is free (it is feature 0), so the model only earns its place
in the pipeline if it beats that, and the number worth quoting is the lift over
it, not the absolute NDCG.

  random        shuffled order, averaged over many shuffles: the floor
  cosine_only   rank by feature 0 (goal/POI embedding cosine), no model
  lambdarank    the full model, trained on all the other cities

Folds are whole cities, never rows or queries. The three queries of a city
share the same POIs, so any finer split would let the model see the test
places during training.

Two views of each held-out city are scored:

  full_pool   every candidate, all eight categories. This is the headline
              number and what --check gates on.
  goal_pool   only the candidates the app would actually fetch for those goals
              (overpass.CATEGORY_QUERIES). In production the fetch has already
              thrown away most off-topic places, so this view is harder to win
              and closer to what a user gets.

What the numbers do not show. Labels are silver: an LLM judge graded each
(goals, place) pair (bench/label_pois.py), so this measures agreement with that
judge, not user satisfaction. The judge saw the place's category, and the model
has category-match features, so part of the full_pool lift is the model
learning the judge's own rule. And n is small: one fold per labelled city (the
report prints n). Read the per-fold range before the mean.

Hyperparameters were fixed before the first run (see model.py) and nothing is
tuned on these folds.

Cities are whatever bench/labels/ holds, so the benchmark grows with the
labelled set without changes here.

Run:   python -m backend.bench.ranker_bench
CI:    python -m backend.bench.ranker_bench --check   (exit 1 if the model does
       not beat cosine_only on held-out mean NDCG@10)
Needs no network beyond the embedding model download.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

from backend.ml.ranker.features import FEATURE_NAMES, extract_features  # before model (libomp)
from backend.ml.ranker.metrics import KS, evaluate_scores
from backend.ml.ranker.model import POIRanker
from backend.ml.ranker.trainer import LABEL_DIR, load_labelled_queries
from backend.services.overpass import CATEGORY_QUERIES

RESULTS_PATH = Path(__file__).resolve().parent / "results" / "ranker.json"
MODELS = ["random", "cosine_only", "lambdarank"]
METRICS = [f"{m}@{k}" for m in ("ndcg", "precision") for k in KS]
CHECK_METRIC = "ndcg@10"
N_RANDOM_TRIALS = 50
SEED = 0


def in_goal_pool(poi: dict, goals: list[str]) -> bool:
    """Would the app's Overpass query for these goals have returned this POI?"""
    for goal in goals:
        for tag_expr in CATEGORY_QUERIES.get(goal, []):
            key, pattern = tag_expr.split("~")
            if re.search(pattern.strip('"'), poi["tags"].get(key, "")):
                return True
    return False


def featurise(queries: list[dict]) -> list[dict]:
    """Add X (features) to each query. Embeddings are cached, so repeats are cheap."""
    return [{**q, "X": extract_features(q["goals"], q["pois"])} for q in queries]


def goal_pool_view(queries: list[dict]) -> list[dict]:
    """The same queries with candidates cut down to what production would fetch."""
    out = []
    for q in queries:
        keep = [i for i, p in enumerate(q["pois"]) if in_goal_pool(p, q["goals"])]
        out.append({**q, "pois": [q["pois"][i] for i in keep], "labels": q["labels"][keep]})
    return featurise(out)


def score_queries(queries: list[dict], ranker: POIRanker, rng: np.random.Generator) -> dict:
    """
    Score one held-out city's queries with every model.
    Returns {model: {"mean": {metric: value}, "per_query": [ndcg@10 per query]}}.
    """
    X = np.vstack([q["X"] for q in queries])
    y = np.concatenate([q["labels"] for q in queries])
    groups = [len(q["pois"]) for q in queries]
    singles = [(q["labels"], q["X"]) for q in queries]

    def one(score_fn) -> dict:
        return {"mean": evaluate_scores(y, score_fn(X), groups),
                "per_query": [evaluate_scores(yq, score_fn(Xq), [len(yq)])[CHECK_METRIC]
                              for yq, Xq in singles]}

    out = {"cosine_only": one(lambda X: X[:, 0]), "lambdarank": one(ranker.score)}
    trials = [one(lambda X: rng.random(len(X))) for _ in range(N_RANDOM_TRIALS)]
    out["random"] = {
        "mean": {m: float(np.mean([t["mean"][m] for t in trials])) for m in METRICS},
        "per_query": np.mean([t["per_query"] for t in trials], axis=0).tolist(),
    }
    return out


def summarise(folds: dict) -> dict:
    """folds: {city: {model: {"mean", "per_query"}}} -> the numbers that get reported."""
    summary = {"per_fold": {city: {m: r[m]["mean"] for m in MODELS} for city, r in folds.items()}}
    for stat, fn in (("mean", np.mean), ("min", np.min), ("max", np.max)):
        summary[stat] = {m: {metric: float(fn([r[m]["mean"][metric] for r in folds.values()]))
                             for metric in METRICS} for m in MODELS}
    base, model = summary["mean"]["cosine_only"], summary["mean"]["lambdarank"]
    summary["lift_over_cosine_only"] = {
        metric: {"absolute": model[metric] - base[metric],
                 "relative_pct": (model[metric] - base[metric]) / base[metric] * 100 if base[metric] else None}
        for metric in METRICS}
    # How often the model wins, which says more than a mean when n is 8
    summary["folds_won"] = sum(r["lambdarank"]["mean"][CHECK_METRIC] > r["cosine_only"]["mean"][CHECK_METRIC]
                               for r in folds.values())
    wins = [a > b for r in folds.values()
            for a, b in zip(r["lambdarank"]["per_query"], r["cosine_only"]["per_query"])]
    summary["queries_won"], summary["n_queries"] = int(sum(wins)), len(wins)
    return summary


def run() -> dict:
    full = featurise(load_labelled_queries())
    goal = goal_pool_view(full)
    cities = sorted({q["city"] for q in full})
    rng = np.random.default_rng(SEED)

    folds = {"full_pool": {}, "goal_pool": {}}
    for held_out in cities:
        train = [q for q in full if q["city"] != held_out]
        ranker = POIRanker()
        ranker.train(np.vstack([q["X"] for q in train]),
                     np.concatenate([q["labels"] for q in train]),
                     [len(q["pois"]) for q in train])
        for view, queries in (("full_pool", full), ("goal_pool", goal)):
            folds[view][held_out] = score_queries([q for q in queries if q["city"] == held_out], ranker, rng)

    labels = np.concatenate([q["labels"] for q in full]).astype(int)
    judge = json.loads(next(iter(sorted(LABEL_DIR.glob("*.json")))).read_text())["judge"]["model"]
    return {
        "protocol": "leave-one-city-out cross validation; per-fold value is the mean over that city's queries",
        "label_source": f"silver labels from an LLM judge ({judge}), graded 0 to 4",
        "n_cities": len(cities),
        "n_queries": len(full),
        "n_pairs": {"full_pool": int(len(labels)), "goal_pool": int(sum(len(q["pois"]) for q in goal))},
        "label_distribution": {str(level): int((labels == level).sum()) for level in range(5)},
        "features": FEATURE_NAMES,
        "model_params": {k: v for k, v in POIRanker().model.get_params().items() if v is not None},
        "check": {"view": "full_pool", "metric": CHECK_METRIC},
        "full_pool": summarise(folds["full_pool"]),
        "goal_pool": summarise(folds["goal_pool"]),
    }


def report(results: dict) -> None:
    print("\n" + "=" * 100)
    print(f"RANKER: leave-one-city-out CV, n = {results['n_cities']} folds (cities), "
          f"{results['n_queries']} queries, {results['n_pairs']['full_pool']} labelled (goals, POI) pairs")
    print(f"Labels: {results['label_source']}; distribution {results['label_distribution']}")
    print("=" * 100)
    for view in ("full_pool", "goal_pool"):
        s = results[view]
        print(f"\n{view} ({results['n_pairs'][view]} pairs)   mean over folds [min fold, max fold]")
        print(f"{'model':<13}" + "".join(f"{m:>22}" for m in METRICS))
        print("-" * 100)
        for model in MODELS:
            cells = [f"{s['mean'][model][m]:.3f} [{s['min'][model][m]:.3f}, {s['max'][model][m]:.3f}]"
                     for m in METRICS]
            print(f"{model:<13}" + "".join(f"{c:>22}" for c in cells))
        print("-" * 100)
        for m in METRICS:
            lift = s["lift_over_cosine_only"][m]
            print(f"lambdarank vs cosine_only, {m}: {lift['absolute']:+.3f} absolute, {lift['relative_pct']:+.1f}% relative")
        print(f"lambdarank beats cosine_only on {CHECK_METRIC} in {s['folds_won']}/{results['n_cities']} folds "
              f"and {s['queries_won']}/{s['n_queries']} queries")
        print(f"\n  per fold, {CHECK_METRIC}:")
        for city, r in s["per_fold"].items():
            print(f"    {city:<20}" + "".join(f"{model} {r[model][CHECK_METRIC]:.3f}   " for model in MODELS))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="exit 1 unless lambdarank beats cosine_only on held-out mean NDCG@10")
    args = parser.parse_args()

    results = run()
    report(results)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nResults written to {RESULTS_PATH}")

    if args.check:
        mean = results["full_pool"]["mean"]
        model, base = mean["lambdarank"][CHECK_METRIC], mean["cosine_only"][CHECK_METRIC]
        if model <= base:
            print(f"CHECK FAILED: lambdarank {CHECK_METRIC} {model:.3f} does not beat cosine_only {base:.3f}")
            sys.exit(1)
        print(f"CHECK PASSED: lambdarank {CHECK_METRIC} {model:.3f} > cosine_only {base:.3f}")


if __name__ == "__main__":
    main()
