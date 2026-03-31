"""
Day scheduler: assigns ranked POIs across trip days, then orders each day's route.

Strategy:
  1. Estimate total visit + travel time for all POIs
  2. Greedily assign POIs to days respecting daily time budget
  3. Order each day's stops by time-of-day logic (nature→morning, culture→afternoon, nightlife→evening)
     with food spread across the day and greedy nearest-neighbor within each bucket
  4. Return structured day-by-day itinerary
"""

import numpy as np
from .vrp import VISIT_DURATION, DAY_START_SEC
from .distance import build_time_matrix

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
    if len(food_indices) == 1:
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


def build_itinerary(
    pois: list[dict],
    n_days: int,
    transport: str,
    start_lat: float = None,
    start_lon: float = None,
    start_hour: int = 9,
) -> list[dict]:
    """
    Full scheduling pipeline:
    1. Build travel time matrix (optionally anchored to start location)
    2. Assign POIs to days
    3. Order each day's stops by time-of-day bucket + greedy nearest-neighbor
    4. Return structured itinerary
    """
    if not pois:
        return []

    # Prepend start location as a virtual depot node (index 0)
    if start_lat is not None and start_lon is not None:
        depot = {"lat": start_lat, "lon": start_lon, "category": "_depot", "name": "_start"}
        all_nodes = [depot] + pois
        full_matrix = build_time_matrix(all_nodes, transport)
        # Time matrix for just the real POIs
        time_matrix = full_matrix[1:, 1:]
        # Travel time from depot to each POI (used to anchor day 1)
        depot_to_poi = full_matrix[0, 1:]
    else:
        time_matrix = build_time_matrix(pois, transport)
        depot_to_poi = None

    day_assignments = assign_days(pois, n_days, time_matrix)

    day_start_sec = start_hour * 3600

    itinerary = []
    for day_num, poi_indices in enumerate(day_assignments):
        if not poi_indices:
            continue

        day_pois = [pois[i] for i in poi_indices]
        day_matrix = time_matrix[np.ix_(poi_indices, poi_indices)]

        # For day 1, seed the TSP so the closest POI to the start location goes first
        if day_num == 0 and depot_to_poi is not None:
            depot_times = depot_to_poi[poi_indices]
            closest = int(np.argmin(depot_times))
        else:
            closest = 0

        optimized_order = _time_aware_order(day_pois, day_matrix, start_node=closest)
        ordered_pois = [day_pois[i] for i in optimized_order]

        # compute travel times between consecutive stops
        travel_times = []
        for a, b in zip(optimized_order, optimized_order[1:]):
            travel_times.append(int(day_matrix[a][b]))

        # estimate arrival times from start_hour on day 1, 9am on subsequent days
        current_time = day_start_sec if day_num == 0 else DAY_START_SEC
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
