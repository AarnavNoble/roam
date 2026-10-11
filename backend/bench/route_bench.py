"""
Route ordering benchmark.

Five ways of ordering one day's stops, on days sampled from real POI pools:

  rank_order      stops in relevance order, no spatial reasoning. The floor.
  greedy_nn       nearest neighbour from the start location, ignoring time of day
  time_aware      the heuristic that used to ship: bucket by time of day, then
                  nearest neighbour inside each bucket
  ortools         what ships now (vrp.solve_day): shortest path subject to soft
                  time-of-day windows
  ortools_free    the same solver with the windows switched off. A lower bound
                  on travel, since it optimises travel and nothing else.

Each is scored on two things, because either alone is easy to win: minutes of
travel (start location to first stop, then stop to stop) and minutes by which
arrivals fall outside their preferred windows (vrp.preferred_windows).

Days are sampled with a fixed seed from each fixture's pool, restricted to the
scenario's goal categories, so the benchmark does not depend on the ranker.

Run: python -m backend.bench.route_bench [--sweep]
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from backend.bench.fixtures import load_all
from backend.ml.optimizer.distance import haversine_matrix
from backend.ml.optimizer.scheduler import _greedy_nn, _time_aware_order
from backend.ml.optimizer.vrp import DAY_START_SEC, solve_day, window_violation_s

RESULTS_PATH = Path(__file__).resolve().parent / "results" / "route.json"
DAY_SIZES = [4, 6, 9]          # stops per day at relaxed / moderate / packed pace
DAYS_PER_SIZE = 5              # sampled days per scenario per size
SWEEP_S = [0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0]
WEIGHT_SWEEP = [1, 2, 4, 6, 8, 16]
SEED = 0

GOAL_TO_CAT = {"adventure": "attraction", "hidden_gems": "attraction"}


def sample_days(scenarios: list[dict]) -> list[dict]:
    """[{scenario, pois, matrix, depot_times}] with a fixed seed."""
    rng = np.random.default_rng(SEED)
    days = []
    for sc in scenarios:
        cats = {GOAL_TO_CAT.get(g, g) for g in sc["goals"]}
        pool = [p for p in sc["pois"] if p.get("category") in cats]
        depot = {"lat": sc["start_lat"], "lon": sc["start_lon"]}
        for size in DAY_SIZES:
            if len(pool) < size:
                continue
            for _ in range(DAYS_PER_SIZE):
                pois = [pool[i] for i in rng.choice(len(pool), size=size, replace=False)]
                full = haversine_matrix([depot] + pois, sc["transport"])
                days.append({"scenario": sc["name"], "pois": pois,
                             "matrix": full[1:, 1:], "depot_times": full[0, 1:]})
    return days


def order_rank(day):
    return list(range(len(day["pois"])))


def order_greedy(day):
    first = int(np.argmin(day["depot_times"]))
    rest = [i for i in range(len(day["pois"])) if i != first]
    return [first] + _greedy_nn(rest, day["matrix"], first)


def order_time_aware(day):
    return _time_aware_order(day["pois"], day["matrix"], start_node=int(np.argmin(day["depot_times"])))


def order_ortools(day, time_limit_s=None, window_penalty=None):
    kwargs = {}
    if time_limit_s is not None:
        kwargs["time_limit_s"] = time_limit_s
    if window_penalty is not None:
        kwargs["window_penalty"] = window_penalty
    order = solve_day(day["pois"], day["matrix"], day["depot_times"], DAY_START_SEC, **kwargs)
    return order if order is not None else order_time_aware(day)


STRATEGIES = {
    "rank_order": order_rank,
    "greedy_nn": order_greedy,
    "time_aware": order_time_aware,
    "ortools": order_ortools,
    "ortools_free": lambda day: order_ortools(day, window_penalty=0),
}


def travel_s(day: dict, order: list[int]) -> int:
    legs = sum(int(day["matrix"][a][b]) for a, b in zip(order, order[1:]))
    return int(day["depot_times"][order[0]]) + legs


def violation_s(day: dict, order: list[int]) -> int:
    return window_violation_s(day["pois"], order, day["matrix"], day["depot_times"], DAY_START_SEC)


def compare(days: list[dict]) -> dict:
    out = {}
    for name, fn in STRATEGIES.items():
        travel = viol = 0
        t0 = time.perf_counter()
        for day in days:
            order = fn(day)
            assert sorted(order) == list(range(len(day["pois"]))), f"{name} dropped a stop"
            travel += travel_s(day, order)
            viol += violation_s(day, order)
        out[name] = {"travel_min": round(travel / 60, 1), "window_violation_min": round(viol / 60, 1),
                     "ms_per_day": round((time.perf_counter() - t0) * 1000 / len(days), 2)}
    return out


def sweep(days: list[dict]) -> list[dict]:
    """Does a longer solver budget buy a better route? Objective = travel + violation."""
    rows = []
    for limit in SWEEP_S:
        cost = 0
        t0 = time.perf_counter()
        for day in days:
            order = order_ortools(day, time_limit_s=limit)
            cost += travel_s(day, order) + violation_s(day, order)
        rows.append({"time_limit_s": limit, "objective_min": round(cost / 60, 1),
                     "ms_per_day": round((time.perf_counter() - t0) * 1000 / len(days), 1)})
        print(f"  limit={limit:>5}s  objective={rows[-1]['objective_min']:>8} min  "
              f"{rows[-1]['ms_per_day']:>7} ms/day")
    return rows


def weight_sweep(days: list[dict]) -> list[dict]:
    """The travel / timing trade-off as TRAVEL_WEIGHT moves. Each row is one point on the curve."""
    rows = []
    for weight in WEIGHT_SWEEP:
        travel = viol = 0
        for day in days:
            order = solve_day(day["pois"], day["matrix"], day["depot_times"], DAY_START_SEC,
                              travel_weight=weight) or order_time_aware(day)
            travel += travel_s(day, order)
            viol += violation_s(day, order)
        rows.append({"travel_weight": weight, "travel_min": round(travel / 60, 1),
                     "window_violation_min": round(viol / 60, 1)})
        print(f"  weight={weight:>3}  travel={rows[-1]['travel_min']:>8} min  "
              f"off-window={rows[-1]['window_violation_min']:>9} min")
    return rows


def report(results: dict, n_days: int) -> None:
    base = results["time_aware"]
    print("\n" + "=" * 78)
    print(f"ROUTE ORDERING over {n_days} sampled days")
    print("=" * 78)
    print(f"{'strategy':<14}{'travel (min)':>14}{'vs time_aware':>15}{'off-window (min)':>18}{'ms/day':>10}")
    print("-" * 78)
    for name, r in results.items():
        delta = (r["travel_min"] - base["travel_min"]) / base["travel_min"] * 100
        print(f"{name:<14}{r['travel_min']:>14.1f}{delta:>+14.1f}%{r['window_violation_min']:>18.1f}"
              f"{r['ms_per_day']:>10.2f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true",
                        help="also sweep the solver time limit and the travel weight")
    args = parser.parse_args()

    scenarios = [s for s in load_all() if s["pois"]]
    days = sample_days(scenarios)
    print(f"{len(scenarios)} scenarios, {len(days)} sampled days")
    results = compare(days)
    report(results, len(days))

    sweep_rows, weight_rows = [], []
    if args.sweep:
        print("\nSOLVER TIME LIMIT SWEEP")
        sweep_rows = sweep(days)
        print("\nTRAVEL WEIGHT SWEEP")
        weight_rows = weight_sweep(days)

    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(
        {"days": len(days), "scenarios": len(scenarios), "strategies": results, "sweep": sweep_rows,
         "weight_sweep": weight_rows}, indent=2))
    print(f"\nWritten to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
