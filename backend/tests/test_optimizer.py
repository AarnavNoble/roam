import numpy as np
from backend.ml.optimizer.distance import haversine_km, haversine_matrix
from backend.ml.optimizer.vrp import solve_tsp
from backend.ml.optimizer.scheduler import assign_days, build_itinerary, build_itinerary_with_meta, _seconds_to_time


SAMPLE_POIS = [
    {"name": "Place A", "lat": 35.68, "lon": 139.76, "category": "food"},
    {"name": "Place B", "lat": 35.69, "lon": 139.77, "category": "history"},
    {"name": "Place C", "lat": 35.70, "lon": 139.75, "category": "nature"},
    {"name": "Place D", "lat": 35.67, "lon": 139.78, "category": "culture"},
]


def test_haversine_zero_distance():
    assert haversine_km(35.68, 139.76, 35.68, 139.76) == 0.0


def test_haversine_known_distance():
    # Tokyo to Osaka is ~400km
    dist = haversine_km(35.68, 139.76, 34.69, 135.50)
    assert 390 < dist < 420


def test_haversine_matrix_shape():
    matrix = haversine_matrix(SAMPLE_POIS, "walking")
    assert matrix.shape == (4, 4)
    # diagonal should be 0
    assert all(matrix[i][i] == 0 for i in range(4))


def test_haversine_matrix_symmetric():
    matrix = haversine_matrix(SAMPLE_POIS, "walking")
    for i in range(4):
        for j in range(4):
            assert abs(matrix[i][j] - matrix[j][i]) < 2  # within 2 seconds


def test_solve_tsp_returns_valid_order():
    matrix = haversine_matrix(SAMPLE_POIS, "walking")
    order = solve_tsp(SAMPLE_POIS, matrix)
    assert len(order) == len(SAMPLE_POIS)
    assert sorted(order) == list(range(len(SAMPLE_POIS)))


def test_solve_tsp_single_poi():
    order = solve_tsp(SAMPLE_POIS[:1], np.zeros((1, 1), dtype=np.int32))
    assert order == [0]


def test_assign_days_respects_day_count():
    matrix = haversine_matrix(SAMPLE_POIS, "walking")
    days = assign_days(SAMPLE_POIS, n_days=2, time_matrix=matrix)
    assert len(days) == 2


def test_build_itinerary_structure():
    itinerary = build_itinerary(SAMPLE_POIS, n_days=2, transport="walking")
    assert len(itinerary) > 0
    for day in itinerary:
        assert "day" in day
        assert "pois" in day
        assert "travel_times_sec" in day
        assert len(day["pois"]) > 0


def test_seconds_to_time():
    assert _seconds_to_time(9 * 3600) == "09:00"
    assert _seconds_to_time(13 * 3600 + 30 * 60) == "13:30"


def test_arrival_times_in_itinerary():
    itinerary = build_itinerary(SAMPLE_POIS, n_days=1, transport="walking")
    for stop in itinerary[0]["pois"]:
        assert "arrival_time" in stop
        assert ":" in stop["arrival_time"]


def test_day_without_food_stops():
    # Regression: a day with zero food stops used to raise IndexError
    no_food = [p for p in SAMPLE_POIS if p["category"] != "food"]
    itinerary = build_itinerary(no_food, n_days=1, transport="walking")
    assert len(itinerary[0]["pois"]) == len(no_food)


def test_short_trip_is_trimmed_to_its_duration():
    # Regression: a 3-hour trip was scheduled across a full day
    budget = 3 * 3600
    route, meta = build_itinerary_with_meta(SAMPLE_POIS, n_days=1, transport="walking",
                                            start_lat=35.68, start_lon=139.76, day_budget_sec=budget)
    stops = route[0]["pois"]
    assert 1 <= len(stops) < len(SAMPLE_POIS)
    assert meta["trimmed"] == len(SAMPLE_POIS) - len(stops)
    assert stops[0]["name"] in {p["name"] for p in SAMPLE_POIS[:len(stops)]}  # highest ranked kept
    h, m = map(int, stops[-1]["arrival_time"].split(":"))
    assert (h * 60 + m) * 60 + stops[-1]["visit_duration_min"] * 60 <= 9 * 3600 + budget


def test_solver_orders_nightlife_after_morning_stops():
    pois = [
        {"name": "Bar", "lat": 35.6801, "lon": 139.7601, "category": "nightlife"},
        {"name": "Park", "lat": 35.70, "lon": 139.75, "category": "nature"},
        {"name": "Museum", "lat": 35.69, "lon": 139.77, "category": "culture"},
    ]
    route = build_itinerary(pois, n_days=1, transport="walking", start_lat=35.68, start_lon=139.76)
    names = [p["name"] for p in route[0]["pois"]]
    # the bar is next to the start, but its evening window pushes it to the end
    assert names[-1] == "Bar"
