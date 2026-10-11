"""
Route ordering benchmark.

Compares the travel cost of four strategies for ordering a day's stops:

  rank_order   POIs in relevance order, no spatial reasoning at all.
               This is the honest "no optimizer" baseline.
  greedy_nn    Global greedy nearest-neighbour from the start location.
  time_aware   What the app actually ships (scheduler._time_aware_order):
               bucket by time of day, greedy nearest-neighbour inside each bucket.
  ortools      Unconstrained OR-Tools TSP (vrp.solve_tsp). Effectively a lower
               bound on travel time, since it optimises travel and nothing else.

The gap between time_aware and ortools is the price paid for scheduling
nature in the morning and nightlife at night. The gap between rank_order and
time_aware is what the scheduling layer earns over shipping nothing.

Run: python -m backend.bench.route_bench
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from backend.bench.fixtures import load_all
from backend.ml.optimizer.distance import build_time_matrix
from backend.ml.optimizer.scheduler import assign_days, _greedy_nn, _time_aware_order
from backend.ml.optimizer.vrp import solve_tsp

RESULTS_PATH = Path(__file__).resolve().parent / "results_route.json"
SOLVER_SWEEP_S = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]


def _n_days(duration_hours: int) -> int:
    import math
    return max(1, math.ceil(duration_hours / 10))


def tour_cost(order: list[int], matrix: np.ndarray) -> int:
    """Total travel seconds for a visit order."""
    return int(sum(matrix[a][b] for a, b in zip(order, order[1:])))


def strategy_rank_order(pois, matrix, start_node):
    return list(range(len(pois)))


def strategy_greedy_nn(pois, matrix, start_node):
    return _greedy_nn(list(range(len(pois))), matrix, start_node)


def strategy_time_aware(pois, matrix, start_node):
    return _time_aware_order(pois, matrix, start_node=start_node)


def strategy_ortools(pois, matrix, start_node, time_limit_s=10.0):
    return solve_tsp(pois, matrix, start_node=start_node, time_limit_s=time_limit_s)


STRATEGIES = {
    "rank_order": strategy_rank_order,
    "greedy_nn": strategy_greedy_nn,
    "time_aware": strategy_time_aware,
    "ortools": strategy_ortools,
}


def build_days(scenario: dict) -> list[tuple[list[dict], np.ndarray, int]]:
    """
    Reproduce the scheduler's day assignment, returning per-day
    (pois, day_matrix, start_node) tuples ready for any ordering strategy.
    """
    pois = scenario["pois"]
    if not pois:
        return []

    depot = {"lat": scenario["start_lat"], "lon": scenario["start_lon"],
             "category": "_depot", "name": "_start"}
    full_matrix = build_time_matrix([depot] + pois, scenario["transport"])
    time_matrix = full_matrix[1:, 1:]
    depot_to_poi = full_matrix[0, 1:]

    n_days = _n_days(scenario["duration_hours"])
    day_assignments = assign_days(pois, n_days, time_matrix)

    days = []
    for day_num, indices in enumerate(day_assignments):
        if not indices:
            continue
        day_pois = [pois[i] for i in indices]
        day_matrix = time_matrix[np.ix_(indices, indices)]
        if day_num == 0:
            start_node = int(np.argmin(depot_to_poi[indices]))
        else:
            start_node = 0
        days.append((day_pois, day_matrix, start_node))
    return days


def run_comparison(scenarios: list[dict], solver_time_s: float) -> dict:
    """Run every strategy over every scenario day. Returns per-strategy totals."""
    per_strategy = {name: {"travel_s": [], "wall_s": []} for name in STRATEGIES}
    per_scenario = []

    for scenario in scenarios:
        days = build_days(scenario)
        if not days:
            print(f"  {scenario['name']}: no POIs, skipped")
            continue

        row = {"name": scenario["name"], "n_pois": len(scenario["pois"]),
               "n_days": len(days), "strategies": {}}

        for name, fn in STRATEGIES.items():
            total_travel = 0
            total_wall = 0.0
            for day_pois, day_matrix, start_node in days:
                t0 = time.perf_counter()
                if name == "ortools":
                    order = fn(day_pois, day_matrix, start_node, time_limit_s=solver_time_s)
                else:
                    order = fn(day_pois, day_matrix, start_node)
                total_wall += time.perf_counter() - t0
                total_travel += tour_cost(order, day_matrix)

            per_strategy[name]["travel_s"].append(total_travel)
            per_strategy[name]["wall_s"].append(total_wall)
            row["strategies"][name] = {"travel_s": total_travel, "wall_s": round(total_wall, 4)}

        stops = sum(len(d[0]) for d in days)
        print(f"  {scenario['name']:<18} {len(scenario['pois']):>3} POIs, "
              f"{stops:>2} stops, {len(days)} day(s)")
        per_scenario.append(row)

    return {"per_strategy": per_strategy, "per_scenario": per_scenario}


def solver_sweep(scenarios: list[dict]) -> list[dict]:
    """
    Sweep the OR-Tools time limit to find where tour quality plateaus.
    Answers whether the hard-coded 10s budget buys anything.
    """
    all_days = []
    for scenario in scenarios:
        all_days.extend(build_days(scenario))
    if not all_days:
        return []

    rows = []
    for limit in SOLVER_SWEEP_S:
        total_travel = 0
        total_wall = 0.0
        for day_pois, day_matrix, start_node in all_days:
            t0 = time.perf_counter()
            order = solve_tsp(day_pois, day_matrix, start_node=start_node, time_limit_s=limit)
            total_wall += time.perf_counter() - t0
            total_travel += tour_cost(order, day_matrix)
        rows.append({
            "time_limit_s": limit,
            "total_travel_s": total_travel,
            "total_wall_s": round(total_wall, 3),
            "wall_per_call_s": round(total_wall / len(all_days), 3),
        })
        print(f"  limit={limit:>5}s  travel={total_travel // 60:>4} min  "
              f"wall={total_wall:>6.2f}s  ({total_wall / len(all_days):.3f}s/call)")
    return rows


def report(comparison: dict) -> None:
    per_strategy = comparison["per_strategy"]
    baseline = sum(per_strategy["rank_order"]["travel_s"])

    print("\n" + "=" * 68)
    print("ROUTE ORDERING — total travel time across all scenarios")
    print("=" * 68)
    print(f"{'strategy':<14}{'travel (min)':>14}{'vs rank_order':>16}{'wall (s)':>12}")
    print("-" * 68)
    for name in STRATEGIES:
        travel = sum(per_strategy[name]["travel_s"])
        wall = sum(per_strategy[name]["wall_s"])
        delta = (travel - baseline) / baseline * 100 if baseline else 0.0
        marker = "" if name == "rank_order" else f"{delta:+.1f}%"
        print(f"{name:<14}{travel / 60:>14.1f}{marker:>16}{wall:>12.2f}")

    ta = sum(per_strategy["time_aware"]["travel_s"])
    ot = sum(per_strategy["ortools"]["travel_s"])
    if ot:
        print("-" * 68)
        print(f"time_aware costs {(ta - ot) / ot * 100:+.1f}% travel vs unconstrained TSP")
        print(f"  (the price of time-of-day scheduling)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver-time", type=float, default=10.0,
                        help="OR-Tools time limit for the main comparison")
    parser.add_argument("--sweep", action="store_true",
                        help="Also sweep solver time limits")
    args = parser.parse_args()

    scenarios = load_all()
    print(f"Loaded {len(scenarios)} scenarios\n")

    comparison = run_comparison(scenarios, args.solver_time)
    report(comparison)

    sweep = []
    if args.sweep:
        print("\n" + "=" * 68)
        print("SOLVER TIME LIMIT SWEEP")
        print("=" * 68)
        sweep = solver_sweep(scenarios)

    RESULTS_PATH.write_text(json.dumps(
        {"comparison": comparison["per_scenario"], "sweep": sweep}, indent=2))
    print(f"\nResults written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
