"""
Day scheduler: assigns ranked POIs across trip days, then optimizes each day's route.

Strategy:
  1. Estimate total visit + travel time for all POIs
  2. Greedily assign POIs to days respecting daily time budget
  3. Run TSP solver on each day's POI subset
  4. Return structured day-by-day itinerary
"""

import numpy as np
from .vrp import solve_tsp, VISIT_DURATION, DAY_START_SEC
from .distance import build_time_matrix

DAILY_BUDGET_SEC = 10 * 3600  # 10 hours of activity per day


def _estimate_day_duration(pois: list[dict], time_matrix: np.ndarray, indices: list[int]) -> int:
    """Estimate total time (travel + visits) for a subset of POIs."""
    if not indices:
        return 0
    total = sum(VISIT_DURATION.get(pois[i].get("category", "attraction"), 45 * 60) for i in indices)
    # add travel: sum of consecutive travel times in current order
    for a, b in zip(indices, indices[1:]):
        total += int(time_matrix[a][b])
    return total


def assign_days(pois: list[dict], n_days: int, time_matrix: np.ndarray) -> list[list[int]]:
    """
    Greedily assign POIs to days.
    POIs are pre-sorted by relevance score (highest first).
    Returns list of lists: day_assignments[day] = [poi_indices]
    """
    days = [[] for _ in range(n_days)]
    day_times = [0] * n_days

    for poi_idx, poi in enumerate(pois):
        visit_time = VISIT_DURATION.get(poi.get("category", "attraction"), 45 * 60)

        # find the day with the most remaining budget
        best_day = None
        best_remaining = -1
        for d in range(n_days):
            remaining = DAILY_BUDGET_SEC - day_times[d]
            if remaining >= visit_time and remaining > best_remaining:
                best_day = d
                best_remaining = remaining

        if best_day is not None:
            days[best_day].append(poi_idx)
            day_times[best_day] += visit_time

    return days


def build_itinerary(pois: list[dict], n_days: int, transport: str) -> list[dict]:
    """
    Full scheduling pipeline:
    1. Build travel time matrix
    2. Assign POIs to days
    3. Optimize each day's route with TSP
    4. Return structured itinerary

    Returns: list of day dicts, each with ordered list of POIs + travel times
    """
    if not pois:
        return []

    time_matrix = build_time_matrix(pois, transport)
    day_assignments = assign_days(pois, n_days, time_matrix)

    itinerary = []
    for day_num, poi_indices in enumerate(day_assignments):
        if not poi_indices:
            continue

        day_pois = [pois[i] for i in poi_indices]
        day_matrix = time_matrix[np.ix_(poi_indices, poi_indices)]

        optimized_order = solve_tsp(day_pois, day_matrix)
        ordered_pois = [day_pois[i] for i in optimized_order]

        # compute travel times between consecutive stops
        travel_times = []
        for a, b in zip(optimized_order, optimized_order[1:]):
            travel_times.append(int(day_matrix[a][b]))

        # estimate arrival times
        current_time = DAY_START_SEC
        for i, poi in enumerate(ordered_pois):
            poi = poi.copy()
            poi["arrival_time"] = _seconds_to_time(current_time)
            visit_dur = VISIT_DURATION.get(poi.get("category", "attraction"), 45 * 60)
            poi["visit_duration_min"] = visit_dur // 60
            current_time += visit_dur
            if i < len(travel_times):
                current_time += travel_times[i]
            ordered_pois[i] = poi

        itinerary.append({
            "day": day_num + 1,
            "pois": ordered_pois,
            "travel_times_sec": travel_times,
            "total_travel_min": sum(travel_times) // 60,
        })

    return itinerary


def _seconds_to_time(seconds: int) -> str:
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"
