"""
OR-Tools routing for one day of stops.

solve_day   what the app ships: an open path from the traveller's start
            location that minimises travel time, with soft time-of-day windows
            (nature in the morning, nightlife at night, meals at meal times).
solve_tsp   travel time only, no windows. Kept as the benchmark's lower bound.

The first version used hard time windows and went infeasible whenever a day
was packed, so the windows were dropped and a greedy heuristic shipped
instead. Soft windows fix the actual problem: a stop outside its window is
allowed, it just costs WINDOW_PENALTY per second, so there is always a
feasible answer and the solver trades walking against timing.
"""

import numpy as np
from ortools.constraint_solver import routing_enums_pb2, pywrapcp


# Default visit durations in seconds by category
VISIT_DURATION = {
    "food":        60 * 60,       # 1 hour
    "history":     90 * 60,       # 1.5 hours
    "culture":     90 * 60,       # 1.5 hours
    "nature":      60 * 60,       # 1 hour
    "nightlife":   2 * 60 * 60,   # 2 hours
    "shopping":    60 * 60,       # 1 hour
    "adventure":   2 * 60 * 60,   # 2 hours
    "attraction":  45 * 60,       # 45 min
}

DAY_START_SEC = 9 * 3600   # 9:00 AM
DAY_END_SEC   = 23 * 3600  # 11:00 PM (generous upper bound)


def solve_tsp(
    pois: list[dict],
    time_matrix: np.ndarray,
    start_node: int = 0,
    time_limit_s: float = 10.0,
) -> list[int]:
    """
    Solve TSP for a single day's POIs using OR-Tools.
    Minimizes total travel time (pure distance objective, no time windows).
    start_node: index of the POI closest to the user's start location.
    time_limit_s: solver budget. Guided local search does not stop on
        convergence, so this is spent in full on every call.
    Returns ordered list of indices into pois.
    """
    n = len(pois)
    if n <= 1:
        return list(range(n))

    manager = pywrapcp.RoutingIndexManager(n, 1, start_node)
    routing = pywrapcp.RoutingModel(manager)

    # Arc cost = travel time only (no visit duration in cost — just minimize travel)
    def transit_callback(from_idx, to_idx):
        return int(time_matrix[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)])

    transit_cb_idx = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromMilliseconds(int(time_limit_s * 1000))

    solution = routing.SolveWithParameters(search_params)

    if not solution:
        return list(range(n))

    order = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        order.append(manager.IndexToNode(idx))
        idx = solution.Value(routing.NextVar(idx))

    return order


# Preferred arrival window per time-of-day bucket, in seconds since midnight.
MORNING = (0, 12 * 3600)
AFTERNOON = (11 * 3600, 18 * 3600)
EVENING = (18 * 3600, 24 * 3600)
BREAKFAST = (0, 10 * 3600 + 1800)
LUNCH = (11 * 3600 + 1800, 14 * 3600 + 1800)
DINNER = (18 * 3600, 21 * 3600)

CATEGORY_WINDOW = {
    "nature": MORNING,        # maximise daylight
    "adventure": MORNING,
    "history": AFTERNOON,
    "culture": AFTERNOON,
    "attraction": AFTERNOON,
    "shopping": AFTERNOON,
    "nightlife": EVENING,
}

# The objective is TRAVEL_WEIGHT * travel seconds + WINDOW_PENALTY * seconds
# outside a window, so the ratio says how much bad timing one minute of
# walking is worth. At 1:1 the solver walked 24% further than the old greedy
# heuristic to fix timing. route_bench sweeps the ratio; 6:1 is where the
# solver beats the heuristic on travel and on timing at once.
WINDOW_PENALTY = 1
TRAVEL_WEIGHT = 6

# Guided local search never stops on its own. route_bench sweeps this limit;
# tour quality is flat from 0.1s up at the sizes a day produces (<= 9 stops).
SOLVER_TIME_LIMIT_S = 0.25


def preferred_windows(pois: list[dict]) -> list[tuple[int, int]]:
    """
    Window per stop. Non-food stops take their category's window. Food stops
    are spread over meals in rank order so they are never back to back:
    one -> lunch, two -> breakfast and dinner, more -> breakfast, lunches, dinner.
    """
    windows = [CATEGORY_WINDOW.get(p.get("category", "attraction"), AFTERNOON) for p in pois]
    food = [i for i, p in enumerate(pois) if p.get("category") == "food"]
    if len(food) == 1:
        windows[food[0]] = LUNCH
    elif len(food) >= 2:
        windows[food[0]] = BREAKFAST
        windows[food[-1]] = DINNER
        for i in food[1:-1]:
            windows[i] = LUNCH
    return windows


def window_violation_s(pois: list[dict], order: list[int], time_matrix: np.ndarray,
                       depot_times: np.ndarray | None, day_start_sec: int) -> int:
    """Total seconds by which arrivals fall outside their preferred windows."""
    windows = preferred_windows(pois)
    now = day_start_sec
    total = 0
    prev = None
    for i in order:
        if prev is None:
            now += int(depot_times[i]) if depot_times is not None else 0
        else:
            now += int(time_matrix[prev][i])
        lo, hi = windows[i]
        total += max(0, lo - now) + max(0, now - hi)
        now += VISIT_DURATION.get(pois[i].get("category", "attraction"), 45 * 60)
        prev = i
    return total


def solve_day(
    pois: list[dict],
    time_matrix: np.ndarray,
    depot_times: np.ndarray | None = None,
    day_start_sec: int = DAY_START_SEC,
    time_limit_s: float = SOLVER_TIME_LIMIT_S,
    window_penalty: int = WINDOW_PENALTY,
    travel_weight: int = TRAVEL_WEIGHT,
) -> list[int] | None:
    """
    Order one day's stops. Returns indices into pois, or None if the solver
    found nothing (the caller then falls back to the greedy heuristic).

    Model: node 0 is the start location, nodes 1..n the stops, node n+1 a
    dummy end that costs nothing to reach, which turns the closed tour OR-Tools
    wants into the open path a traveller actually walks.
      objective  = TRAVEL_WEIGHT * travel time + WINDOW_PENALTY * seconds outside each window
      Time dim   = travel + visit duration, no waiting, starts at day_start_sec
    """
    n = len(pois)
    if n == 0:
        return []
    if n == 1:
        return [0]

    end = n + 1
    service = [0] + [VISIT_DURATION.get(p.get("category", "attraction"), 45 * 60) for p in pois] + [0]

    def travel(a: int, b: int) -> int:
        if b == end or a == end or b == 0:
            return 0
        if a == 0:
            return int(depot_times[b - 1]) if depot_times is not None else 0
        return int(time_matrix[a - 1][b - 1])

    manager = pywrapcp.RoutingIndexManager(n + 2, 1, [0], [end])
    routing = pywrapcp.RoutingModel(manager)

    cost_idx = routing.RegisterTransitCallback(
        lambda i, j: travel_weight * travel(manager.IndexToNode(i), manager.IndexToNode(j)))
    routing.SetArcCostEvaluatorOfAllVehicles(cost_idx)

    time_idx = routing.RegisterTransitCallback(
        lambda i, j: travel(manager.IndexToNode(i), manager.IndexToNode(j))
        + service[manager.IndexToNode(i)])
    horizon = day_start_sec + 48 * 3600
    routing.AddDimension(time_idx, 0, horizon, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    time_dim.CumulVar(routing.Start(0)).SetRange(day_start_sec, day_start_sec)

    for i, (lo, hi) in enumerate(preferred_windows(pois)):
        index = manager.NodeToIndex(i + 1)
        if window_penalty > 0:  # 0 gives the pure shortest path, the benchmark's lower bound
            time_dim.SetCumulVarSoftLowerBound(index, lo, window_penalty)
            time_dim.SetCumulVarSoftUpperBound(index, hi, window_penalty)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    search_params.time_limit.FromMilliseconds(int(time_limit_s * 1000))

    solution = routing.SolveWithParameters(search_params)
    if not solution:
        return None

    order = []
    idx = solution.Value(routing.NextVar(routing.Start(0)))
    while not routing.IsEnd(idx):
        order.append(manager.IndexToNode(idx) - 1)
        idx = solution.Value(routing.NextVar(idx))
    return order
