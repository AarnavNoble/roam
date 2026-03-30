"""
OR-Tools TSP solver for daily route optimization.
Minimizes total travel distance — time windows removed since they caused
infeasible solutions when many stops are packed into a day.
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


def solve_tsp(pois: list[dict], time_matrix: np.ndarray, start_node: int = 0) -> list[int]:
    """
    Solve TSP for a single day's POIs using OR-Tools.
    Minimizes total travel time (pure distance objective, no time windows).
    start_node: index of the POI closest to the user's start location.
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
    search_params.time_limit.seconds = 10

    solution = routing.SolveWithParameters(search_params)

    if not solution:
        return list(range(n))

    order = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        order.append(manager.IndexToNode(idx))
        idx = solution.Value(routing.NextVar(idx))

    return order
