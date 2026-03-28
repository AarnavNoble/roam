"""
OR-Tools VRP solver for daily route optimization.

Formulated as a TSP with time windows per day.
Minimizes total travel time while respecting:
  - Opening/closing hours (time windows)
  - Visit duration per POI
  - Daily time budget
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
DAY_END_SEC   = 21 * 3600  # 9:00 PM


def _build_time_windows(pois: list[dict]) -> list[tuple[int, int]]:
    """Convert POI opening hours to (earliest, latest) in seconds from midnight."""
    windows = []
    for poi in pois:
        tags = poi.get("tags", {})
        opening = tags.get("opening_hours", "")
        # Default: 9am - 9pm. TODO: parse OSM opening_hours string properly.
        windows.append((DAY_START_SEC, DAY_END_SEC))
    return windows


def solve_tsp(pois: list[dict], time_matrix: np.ndarray, start_node: int = 0) -> list[int]:
    """
    Solve TSP for a single day's POIs using OR-Tools.
    start_node: index of the POI closest to the user's start location (depot).
    Returns ordered list of indices into pois.
    """
    n = len(pois)
    if n <= 1:
        return list(range(n))

    time_windows = _build_time_windows(pois)
    visit_durations = [VISIT_DURATION.get(p.get("category", "attraction"), 45 * 60) for p in pois]

    # OR-Tools routing setup — depot set to the closest POI to start location
    manager = pywrapcp.RoutingIndexManager(n, 1, start_node)
    routing = pywrapcp.RoutingModel(manager)

    def time_callback(from_idx, to_idx):
        from_node = manager.IndexToNode(from_idx)
        to_node = manager.IndexToNode(to_idx)
        return int(time_matrix[from_node][to_node]) + visit_durations[from_node]

    transit_cb_idx = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    # Time window dimension
    routing.AddDimension(
        transit_cb_idx,
        slack_max=3600,           # allow 1 hour waiting
        capacity=DAY_END_SEC - DAY_START_SEC,
        fix_start_cumul_to_zero=True,
        name="Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    for i, (earliest, latest) in enumerate(time_windows):
        idx = manager.NodeToIndex(i)
        window_start = max(0, earliest - DAY_START_SEC)
        window_end = latest - DAY_START_SEC
        time_dim.CumulVar(idx).SetRange(window_start, window_end)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.seconds = 5  # 5 second solve limit

    solution = routing.SolveWithParameters(search_params)

    if not solution:
        # fallback: return original order
        return list(range(n))

    # extract route order
    order = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        node = manager.IndexToNode(idx)
        order.append(node)
        idx = solution.Value(routing.NextVar(idx))

    return order
