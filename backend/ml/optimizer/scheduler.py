"""
Day scheduler: assigns ranked POIs across trip days, then orders each day's route.

Strategy:
  1. Build the travel time matrix, anchored on the start location
  2. Greedily assign POIs to days respecting daily time budget
  3. Order each day with OR-Tools (vrp.solve_day): minimise travel subject to
     soft time-of-day windows. If the solver returns nothing, fall back to the
     greedy bucket heuristic below, which is what shipped before.
  4. Return structured day-by-day itinerary
"""

import numpy as np
from backend.core.resilience import Deadline
from backend.core.telemetry import stage
from .vrp import VISIT_DURATION, DAY_START_SEC, solve_day
from .distance import build_time_matrix_with_source

# Time-of-day bucket for each category (0=morning, 1=afternoon, 2=evening)
CATEGORY_BUCKET = {
    "nature":      0,   # maximize daylight — go early
    "adventure":   0,   # outdoor activities suit morning energy
    "history":     1,   # landmarks and sites in the afternoon
    "culture":     1,   # museums/galleries typically afternoon
    "attraction":  1,   # general attractions in the afternoon
    "shopping":    1,   # shops open all day — afternoon slot
    "nightlife":   2,   # bars/clubs in the evening
    # food is handled separately — spread across the day
}

DAILY_BUDGET_SEC = 10 * 3600  # 10 hours of activity per day


def _greedy_nn(indices: list[int], matrix: np.ndarray, anchor: int) -> list[int]:
    """
    Order `indices` by greedy nearest-neighbor starting from `anchor`.
    anchor is a matrix index (the last stop before this bucket, or the start node).
    """
    if not indices:
        return []
    remaining = list(indices)
    current = anchor
    order = []
    while remaining:
        nearest = min(remaining, key=lambda i: matrix[current][i])
        order.append(nearest)
        remaining.remove(nearest)
        current = nearest
    return order


def _time_aware_order(pois: list[dict], time_matrix: np.ndarray, start_node: int = 0) -> list[int]:
    """
    Order POIs so the day flows naturally:
      Morning  (0): nature, adventure
      Afternoon (1): history, culture, attraction, shopping
      Evening  (2): nightlife
      Food: spread — 1 food→lunch, 2 foods→breakfast+dinner, 3+→breakfast/lunches/dinner

    Within each time bucket, greedy nearest-neighbor minimizes walking.
    """
    n = len(pois)
    if n <= 1:
        return list(range(n))

    buckets: dict[int, list[int]] = {0: [], 1: [], 2: []}
    food_indices: list[int] = []

    for i, poi in enumerate(pois):
        cat = poi.get("category", "attraction")
        if cat == "food":
            food_indices.append(i)
        else:
            buckets[CATEGORY_BUCKET.get(cat, 1)].append(i)

    # Spread food stops across the day so they're never back-to-back
    if not food_indices:
        pass                                          # no food stops today
    elif len(food_indices) == 1:
        buckets[1].append(food_indices[0])            # single food → lunch
    elif len(food_indices) == 2:
        buckets[0].append(food_indices[0])            # breakfast
        buckets[2].append(food_indices[1])            # dinner
    else:
        buckets[0].append(food_indices[0])            # breakfast
        buckets[2].append(food_indices[-1])           # dinner
        for fi in food_indices[1:-1]:
            buckets[1].append(fi)                     # lunch(es)

    result: list[int] = []
    anchor = start_node
    for bucket_id in (0, 1, 2):
        if not buckets[bucket_id]:
            continue
        ordered = _greedy_nn(buckets[bucket_id], time_matrix, anchor)
        result.extend(ordered)
        anchor = ordered[-1]

    return result


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


def build_itinerary_with_meta(
    pois: list[dict],
    n_days: int,
    transport: str,
    start_lat: float = None,
    start_lon: float = None,
    start_hour: int = 9,
    deadline: Deadline | None = None,
) -> tuple[list[dict], dict]:
    """
    Full scheduling pipeline. Returns (itinerary, meta) where meta records
    which travel-time source and which ordering method were actually used.
    """
    meta = {"matrix": "none", "ordering": "ortools"}
    if not pois:
        return [], meta

    # Prepend start location as a virtual depot node (index 0)
    if start_lat is not None and start_lon is not None:
        depot = {"lat": start_lat, "lon": start_lon, "category": "_depot", "name": "_start"}
        full_matrix, meta["matrix"] = build_time_matrix_with_source([depot] + pois, transport, deadline)
        time_matrix = full_matrix[1:, 1:]
        depot_to_poi = full_matrix[0, 1:]
    else:
        time_matrix, meta["matrix"] = build_time_matrix_with_source(pois, transport, deadline)
        depot_to_poi = None

    day_assignments = assign_days(pois, n_days, time_matrix)

    itinerary = []
    for day_num, poi_indices in enumerate(day_assignments):
        if not poi_indices:
            continue

        day_pois = [pois[i] for i in poi_indices]
        day_matrix = time_matrix[np.ix_(poi_indices, poi_indices)]
        depot_times = depot_to_poi[poi_indices] if depot_to_poi is not None else None
        # start_hour applies to day 1; later days start at 9am
        day_start = start_hour * 3600 if day_num == 0 else DAY_START_SEC

        with stage("route.solve", stops=len(day_pois)):
            try:
                order = solve_day(day_pois, day_matrix, depot_times, day_start)
            except Exception as e:
                print(f"Route solver failed ({e}), using greedy ordering")
                order = None
        if order is None:
            meta["ordering"] = "greedy_fallback"
            closest = int(np.argmin(depot_times)) if depot_times is not None else 0
            order = _time_aware_order(day_pois, day_matrix, start_node=closest)

        ordered_pois = [day_pois[i] for i in order]
        travel_times = [int(day_matrix[a][b]) for a, b in zip(order, order[1:])]

        current_time = day_start
        if depot_times is not None:
            current_time += int(depot_times[order[0]])
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

    return itinerary, meta


def build_itinerary(pois: list[dict], n_days: int, transport: str, start_lat: float = None,
                    start_lon: float = None, start_hour: int = 9) -> list[dict]:
    return build_itinerary_with_meta(pois, n_days, transport, start_lat, start_lon, start_hour)[0]


def _seconds_to_time(seconds: int) -> str:
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"
