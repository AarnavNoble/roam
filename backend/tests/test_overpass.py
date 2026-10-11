from backend.services.overpass import _is_chain, _infer_category, POI


def test_chain_filter_english():
    assert _is_chain("Starbucks", {}) is True
    assert _is_chain("McDonald's", {}) is True
    assert _is_chain("subway", {}) is True


def test_chain_filter_by_brand_tag():
    assert _is_chain("スターバックス", {"brand": "Starbucks"}) is True
    assert _is_chain("マクドナルド", {"brand": "McDonald's"}) is True


def test_local_place_not_filtered():
    assert _is_chain("Ramen Ichiran", {}) is False
    assert _is_chain("Local Cafe", {}) is False


def test_infer_category_food():
    assert _infer_category({"amenity": "restaurant"}) == "food"
    assert _infer_category({"amenity": "cafe"}) == "food"


def test_infer_category_history():
    assert _infer_category({"historic": "monument"}) == "history"


def test_infer_category_nature():
    assert _infer_category({"leisure": "park"}) == "nature"


def test_infer_category_culture():
    assert _infer_category({"tourism": "museum"}) == "culture"


def test_poi_description():
    poi = POI(id=1, name="Ramen Shop", lat=35.68, lon=139.76, category="food",
              tags={"amenity": "restaurant", "cuisine": "ramen"})
    desc = poi.description
    assert "Ramen Shop" in desc
    assert "ramen" in desc


def test_poi_to_dict():
    poi = POI(id=1, name="Test", lat=35.68, lon=139.76, category="food", tags={})
    d = poi.to_dict()
    assert d["name"] == "Test"
    assert d["lat"] == 35.68
    assert d["category"] == "food"


# ── Opening hours ────────────────────────────────────────────────────────────

import re  # noqa: E402

import pytest  # noqa: E402

from backend.core.resilience import UpstreamError  # noqa: E402
from backend.db.models import PoiCache  # noqa: E402
from backend.db.session import session_scope  # noqa: E402
from backend.services import overpass  # noqa: E402
from backend.services.overpass import (  # noqa: E402
    _build_query, _cache_key, _is_open_during, fetch_pois, fetch_pois_with_source,
)
from backend.tests.conftest import MIRROR_A, MIRROR_B, MIRROR_C, START, overpass_elements  # noqa: E402

MON, WED, SAT = 0, 2, 5


@pytest.mark.parametrize("hours,weekday,start,end,expected", [
    ("", WED, 9, 15, True),                                    # no tag: assume open
    ("24/7", WED, 9, 15, True),
    ("sunrise-sunset", WED, 9, 15, True),                      # unparseable: assume open
    ("Mo-Fr 09:00-18:00", WED, 9, 15, True),
    ("Mo-Fr 09:00-12:00", WED, 13, 18, False),                 # closes before the trip starts
    ("Mo-Fr 18:00-23:00", WED, 9, 15, False),                  # opens after the trip ends
    ("Mo-Fr 09:00-18:00", SAT, 9, 15, False),                  # keeps hours, none of them today
    ("Mo-Fr 09:00-18:00; Sa 10:00-14:00", SAT, 9, 15, True),
    ("Sa-Mo 10:00-14:00", MON, 9, 15, True),                   # day range wrapping the week
    ("We off", WED, 9, 15, False),
    ("Tu off", WED, 9, 15, True),                              # says nothing about Wednesday
    ("09:00-18:00; We off", WED, 9, 15, False),                # the later rule overrides
    ("Mo-Su 09:00-18:00; PH off", WED, 9, 15, True),           # public-holiday rule is ignored
    ("Mo-Su 18:00-00:00", WED, 19, 23, True),                  # closing at midnight
    ("Mo-Fr 09:00-18:00, Sa 10:00-14:00", SAT, 9, 15, True),   # rules separated by a comma
    ("Mo-Fr 09:00-18:00, Sa 10:00-14:00", SAT, 15, 19, False),
    ("Mo-Fr 09:00-18:00, Sa off", SAT, 9, 15, False),
    ("Sa off, Mo-Fr 09:00-18:00", WED, 9, 15, True),
    ("Mo, We 09:00-18:00", WED, 9, 15, True),                  # space inside a day list
    ("Mo, We 09:00-18:00", 1, 9, 15, False),                   # ... which does not include Tuesday
    ("Fr,Sa 23:00-04:00", 4, 17, 23, True),                    # opens as the trip window ends
    ("Fr,Sa 23:00-04:00", 4, 9, 15, False),
    ("Mo-Su 09:00-18:00; Tu off", 1, 9, 19, False),
    ("Mo-Sa 10:00-19:00; Su,PH off", 6, 9, 19, False),
])
def test_opening_hours(hours, weekday, start, end, expected):
    assert _is_open_during(hours, weekday, start, end) is expected


def test_split_hours_are_all_considered():
    # A restaurant open for lunch and dinner. Only the first range used to be read, so an
    # evening trip dropped it.
    hours = "Mo-Su 12:00-14:00,19:00-22:30"
    assert _is_open_during(hours, MON, 18, 23) is True
    assert _is_open_during(hours, MON, 15, 18) is False  # the afternoon gap


def test_hours_past_midnight():
    # A bar open 18:00-02:00 used to parse as closing before it opened and was always dropped.
    assert _is_open_during("Mo-Su 18:00-02:00", MON, 19, 23) is True
    assert _is_open_during("Mo-Su 18:00-02:00", MON, 9, 15) is False


# ── Query construction and cache key ─────────────────────────────────────────

def test_batched_query_has_one_block_per_category():
    query = _build_query(48.88, 2.34, 2000, ["food", "nature", "culture"])
    assert query.startswith("[out:json]")
    # one named set and one limited output per category, in a single request
    assert re.findall(r"->\.c(\d+);", query) == ["0", "1", "2"]
    assert re.findall(r"\.c(\d+) out center (\d+);", query) == [
        ("0", str(overpass.PER_CATEGORY)), ("1", str(overpass.PER_CATEGORY)), ("2", str(overpass.PER_CATEGORY))]
    assert query.count("(around:2000,48.88,2.34)") == 2 * (1 + 2 + 2)  # node + way per tag filter
    assert 'amenity~"restaurant|cafe|bar|fast_food|food_court"' in query
    assert '[!"brand"]' in query  # chains are excluded server-side too
    # every filter asks for named elements only, so unnamed ones cannot use up the per-category cap
    assert query.count('["name"]') == query.count("(around:") == 10


def test_unknown_category_adds_no_block():
    assert _build_query(48.88, 2.34, 2000, ["food", "bogus"]).count("out center") == 1


def test_cache_key_is_a_100m_grid():
    key = _cache_key(48.8867, 2.3431, 2000, ["food", "culture"])
    assert _cache_key(48.88674, 2.34312, 2000, ["food", "culture"]) == key  # same cell (3 decimals)
    assert _cache_key(48.8877, 2.3431, 2000, ["food", "culture"]) != key    # next cell
    assert _cache_key(48.8867, 2.3431, 2000, ["culture", "food"]) == key    # category order is irrelevant
    assert _cache_key(48.8867, 2.3431, 3000, ["food", "culture"]) != key    # radius is part of the key
    assert _cache_key(48.8867, 2.3431, 2000, ["food"]) != key
    assert re.fullmatch(r"[0-9a-f]{64}", key)                               # fits poi_cache.key


# ── Fetching, caching and per-request filters ────────────────────────────────

def fetch(**kwargs):
    kwargs.setdefault("weekday", WED)
    kwargs.setdefault("visit_end_h", 15)
    return fetch_pois_with_source(*START, ["food", "culture"], radius_m=2000, **kwargs)


def test_fetch_parses_nodes_and_ways_and_drops_noise(http):
    pois, source = fetch()
    assert source == "live"
    # no chain, no nameless, no closed. Ways (104, 201) carry negative ids, see the collision test.
    assert [p.id for p in pois] == [101, 102, 103, -104, -201, 202, 203]
    by_id = {p.id: p for p in pois}
    assert (by_id[-104].lat, by_id[-104].lon) == (48.8848, 2.3443)      # a way uses its centre
    assert by_id[101].category == "food" and by_id[203].category == "culture"
    service, kwargs = http.requests[0]
    assert service == MIRROR_A
    assert kwargs["data"]["data"].count("out center") == 2              # one request for both categories
    assert "roam-app" in kwargs["headers"]["User-Agent"]                # overpass-api.de answers 406 without


def test_duplicate_elements_are_returned_once(http):
    http.handlers[MIRROR_A] = http.json({"elements": overpass_elements() + overpass_elements()[:3]})
    assert [p.id for p in fetch()[0]] == [101, 102, 103, -104, -201, 202, 203]


def test_node_and_way_with_the_same_number_are_both_kept(http):
    # OSM numbers nodes and ways separately. Deduplicating on the bare id dropped whichever of
    # the two came second. Both survive, with distinct integer ids (way ids are negated).
    http.handlers[MIRROR_A] = http.json({"elements": [
        {"type": "node", "id": 7, "lat": 48.88, "lon": 2.34, "tags": {"name": "Cafe Sept", "amenity": "cafe"}},
        {"type": "way", "id": 7, "center": {"lat": 48.881, "lon": 2.341},
         "tags": {"name": "Musee Sept", "tourism": "museum"}},
        {"type": "way", "id": 7, "center": {"lat": 48.881, "lon": 2.341},
         "tags": {"name": "Musee Sept", "tourism": "museum"}},  # the same way again, from a second block
    ]})
    pois = fetch()[0]
    assert [(p.id, p.name) for p in pois] == [(7, "Cafe Sept"), (-7, "Musee Sept")]


def test_raw_elements_are_cached_and_filters_applied_per_request(http):
    # The cache holds what Overpass returned, not what one request kept. A second request with a
    # different diet and a different day is answered from the same entry, filtered its own way.
    everyone, source = fetch()
    assert source == "live" and len(everyone) == 7
    vegan, source = fetch(dietary="vegan")
    assert source == "cache"
    assert [p.id for p in vegan] == [103, -201, 202, 203]  # one vegan restaurant; non-food is unaffected
    thursday, source = fetch(weekday=3)
    assert source == "cache"
    assert 503 in [p.id for p in thursday]                # "We off" is open again on Thursday
    assert http.count(MIRROR_A) == 1


def test_nearby_start_points_share_a_cache_entry(http):
    fetch()
    lat, lon = START
    _, source = fetch_pois_with_source(lat + 0.00003, lon, ["culture", "food"], radius_m=2000, weekday=WED)
    assert source == "cache"
    _, source = fetch_pois_with_source(lat, lon, ["food", "culture"], radius_m=3000, weekday=WED)
    assert source == "live"  # a different radius is a different question
    assert http.count(MIRROR_A) == 2


def test_live_answer_is_written_to_poi_cache(http):
    fetch()
    with session_scope() as db:
        row = db.get(PoiCache, _cache_key(*START, 2000, ["food", "culture"]))
        assert [el["id"] for el in row.payload] == [el["id"] for el in overpass_elements()]
        assert row.fetched_at > 0


def test_stale_copy_is_served_when_every_mirror_is_down(http):
    fetch()
    overpass._memory_cache.clear()
    http.break_all_mirrors()
    pois, source = fetch()
    assert source == "stale"
    assert len(pois) == 7


def test_stored_copy_older_than_the_stale_window_is_not_served(http):
    fetch()
    overpass._memory_cache.clear()
    with session_scope() as db:
        db.get(PoiCache, _cache_key(*START, 2000, ["food", "culture"])).fetched_at -= overpass.STALE_TTL_S + 60
    http.break_all_mirrors()
    with pytest.raises(UpstreamError, match="All Overpass mirrors failed"):
        fetch()


def test_empty_answer_is_not_cached(http):
    # Caching "nothing here" would hide the neighbourhood for an hour after one bad answer.
    http.handlers[MIRROR_A] = http.json({"elements": []})
    assert fetch() == ([], "live")
    http.handlers[MIRROR_A] = http.json({"elements": overpass_elements()})
    pois, source = fetch()
    assert source == "live" and len(pois) == 7


def test_failover_order_and_breaker_bookkeeping(http):
    # A is down, B answers: C is never asked, and only A's breaker has a failure against it.
    http.handlers[MIRROR_A] = http.unreachable()
    assert fetch()[1] == "live"
    assert [http.count(m) for m in (MIRROR_A, MIRROR_B, MIRROR_C)] == [1, 1, 0]
    assert overpass._breaker(MIRROR_A)._failures == 1
    assert overpass._breaker(MIRROR_B)._failures == 0


def test_fetch_pois_returns_empty_list_when_overpass_is_unreachable(http):
    # The older helper keeps its contract for callers that cannot handle UpstreamError.
    http.break_all_mirrors()
    assert fetch_pois(*START, ["food"]) == []


# ── Deadline, breakers and the two stale copies ──────────────────────────────

def open_every_breaker():
    for mirror in (MIRROR_A, MIRROR_B, MIRROR_C):
        breaker = overpass._breaker(mirror)
        for _ in range(breaker.failure_threshold):
            breaker.record_failure()
        assert breaker.state == "open"


def test_retry_rounds_share_one_deadline(http, monkeypatch):
    # Every mirror hangs. The timeout handed to each call comes from what is left of the deadline,
    # so three retry rounds cannot each take the full mirror timeout and overrun the request.
    import time as _time
    from backend.core.resilience import Deadline
    monkeypatch.setattr(overpass, "MIRROR_TIMEOUT_S", 0.3)
    monkeypatch.setattr(overpass, "HEDGE_AFTER_S", 0.05)
    http.break_all_mirrors(http.hang(at_most=5.0))
    started = _time.monotonic()
    with pytest.raises(UpstreamError):
        overpass._query_overpass("q", Deadline(0.6))
    elapsed = _time.monotonic() - started
    assert elapsed < 1.0  # it used to be 3 rounds x (0.3s + hedges), about 1.2s and growing with ROUNDS
    timeouts = [kwargs["timeout"] for _, kwargs in http.requests]
    assert all(0 < t <= 0.6 for t in timeouts)
    assert min(timeouts) < 0.3  # a late call was given only what was left


def test_all_breakers_open_serves_stored_copy_without_probing(http):
    # The mirrors all failed moments ago and a copy is stored: answer from it at once.
    fetch()
    overpass._memory_cache.clear()
    open_every_breaker()
    calls_before = len(http.calls)
    pois, source = fetch()
    assert source == "stale" and len(pois) == 7
    assert len(http.calls) == calls_before  # no mirror was asked


def test_all_breakers_open_and_nothing_stored_probes_anyway(http):
    # No copy to fall back on, so refusing would fail the request for certain. The mirrors are
    # tried despite their breakers, and a success closes the breaker again.
    open_every_breaker()
    pois, source = fetch()
    assert source == "live" and len(pois) == 7
    assert http.count(MIRROR_A) == 1
    assert overpass._breaker(MIRROR_A).state == "closed"


def test_expired_memory_entry_is_served_when_mirrors_and_database_are_down(http, monkeypatch):
    # The in-memory entry is past its fresh window (an hour) but inside the stale window (a week).
    # With the mirrors down and the durable copy unreadable, it is still better than a 503.
    from backend.db import session as db_session
    fetch()
    cache = overpass._memory_cache
    aged = cache._clock() + overpass.FRESH_TTL_S + 60
    monkeypatch.setattr(cache, "_clock", lambda: aged)
    assert cache.get(_cache_key(*START, 2000, ["food", "culture"])) is None  # no longer fresh
    http.break_all_mirrors()

    def no_database():
        raise RuntimeError("database unreachable")
    monkeypatch.setattr(db_session, "session_scope", no_database)
    pois, source = fetch()
    assert source == "stale" and len(pois) == 7
