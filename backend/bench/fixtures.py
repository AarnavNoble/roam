"""
Benchmark fixtures: real POI sets fetched once from Overpass and cached to disk.

Caching matters for two reasons: Overpass is rate-limited and occasionally down,
and benchmark numbers are only comparable across runs if the inputs are identical.

Each fixture holds the candidate pool for ALL eight categories around the start
point, not just the scenario's own two goals. The ranker benchmark needs that:
a ranker can only be measured on a pool that contains wrong answers as well as
right ones, and the same pool is reused for several different goal sets (see
bench/label_pois.py). `goals` is still the scenario's own two goals, which is
what the route and latency benchmarks read.

The committed JSON files are also the ranker's training data, so the trainer
and the benchmark never touch the network.

Refresh the cache with:  python -m backend.bench.fixtures --force
"""

import json
import time
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

# (name, city, start_location, goals, duration_hours, transport)
SCENARIOS = [
    ("paris_marais",     "Paris",         "Le Marais",        ["food", "history"],      8,  "walking"),
    ("tokyo_shinjuku",   "Tokyo",         "Shinjuku",         ["food", "nightlife"],    8,  "walking"),
    ("rome_trastevere",  "Rome",          "Trastevere",       ["history", "culture"],   10, "walking"),
    ("london_soho",      "London",        "Soho",             ["nightlife", "food"],    6,  "walking"),
    ("barcelona_gothic", "Barcelona",     "Gothic Quarter",   ["culture", "food"],      8,  "walking"),
    ("nyc_village",      "New York City", "Greenwich Village", ["food", "shopping"],    6,  "walking"),
    ("sydney_bondi",     "Sydney",        "Bondi Beach",      ["nature", "food"],       8,  "walking"),
    ("bangkok_sukhumvit", "Bangkok",      "Sukhumvit",        ["food", "shopping"],     10, "walking"),
]

ALL_CATEGORIES = ["food", "nature", "history", "culture", "nightlife", "shopping",
                  "adventure", "hidden_gems"]
MOBILITY_RADIUS_M = 2000
CHUNK = 2               # categories per Overpass request
FETCH_ATTEMPTS = 5
RETRY_PAUSE_S = 30      # Overpass answers 429/504 in bursts; give it time to recover
REQUEST_PAUSE_S = 5     # be polite between requests


def _fixture_path(name: str) -> Path:
    return FIXTURE_DIR / f"{name}.json"


def _fetch_pool(lat: float, lon: float) -> list[dict]:
    """
    The candidate pool for all eight categories around a point.

    One Overpass query covering all eight reliably hits the server's 25 s
    timeout in a dense city centre, so the pool is fetched two categories at a
    time (the size of a normal app request) and merged by OSM id. Raises
    UpstreamError if any chunk still fails after FETCH_ATTEMPTS tries: a pool
    with a category silently missing would bias the labels.
    """
    from backend.core.resilience import UpstreamError
    from backend.services.overpass import fetch_pois_with_source

    by_id: dict[int, dict] = {}
    for i in range(0, len(ALL_CATEGORIES), CHUNK):
        chunk = ALL_CATEGORIES[i:i + CHUNK]
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                # The visit window is the whole day (9 to 23) for every scenario
                # so the pool does not depend on the scenario's duration: a bar
                # that opens at 18:00 is still a candidate the ranker has to place.
                pois, _source = fetch_pois_with_source(
                    lat, lon, categories=chunk, radius_m=MOBILITY_RADIUS_M,
                    dietary="none", visit_start_h=9, visit_end_h=23,
                    weekday=2,  # fixed weekday so opening-hours filtering is deterministic
                )
                break
            except UpstreamError as e:
                print(f"    {chunk}: attempt {attempt}/{FETCH_ATTEMPTS} failed ({str(e)[:120]})", flush=True)
                if attempt == FETCH_ATTEMPTS:
                    raise
                time.sleep(RETRY_PAUSE_S)
        for p in pois:
            by_id.setdefault(p.id, p.to_dict())
        time.sleep(REQUEST_PAUSE_S)
    return list(by_id.values())


def fetch_scenario(name: str, city: str, start_location: str, goals: list[str],
                   duration_hours: int, transport: str) -> dict:
    """Fetch one scenario live from Nominatim + Overpass. Raises if Overpass is down."""
    from backend.services.nominatim import geocode

    lat, lon = geocode(f"{start_location}, {city}")
    return {
        "name": name,
        "city": city,
        "start_location": start_location,
        "goals": goals,
        "duration_hours": duration_hours,
        "transport": transport,
        "start_lat": lat,
        "start_lon": lon,
        "pool_categories": ALL_CATEGORIES,
        "pois": _fetch_pool(lat, lon),
    }


def _has_pois(path: Path) -> bool:
    try:
        return bool(json.loads(path.read_text()).get("pois"))
    except (OSError, json.JSONDecodeError):
        return False


def refresh(force: bool = False) -> list[str]:
    """
    Fetch and cache every scenario. Skips ones that already have POIs unless
    force. A failed or empty fetch is never written over a file; the scenario
    name is returned instead so the caller can say so.
    """
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    failed = []
    for name, city, start, goals, hours, transport in SCENARIOS:
        path = _fixture_path(name)
        if _has_pois(path) and not force:
            print(f"  {name}: cached, skipping")
            continue
        try:
            data = fetch_scenario(name, city, start, goals, hours, transport)
        except Exception as e:
            print(f"  {name}: FAILED ({type(e).__name__}: {str(e)[:200]})", flush=True)
            failed.append(name)
            continue
        if not data["pois"]:
            print(f"  {name}: FAILED (no POIs returned)", flush=True)
            failed.append(name)
            continue
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"  {name}: {len(data['pois'])} POIs cached", flush=True)
    return failed


def load_all() -> list[dict]:
    """Load every cached scenario. Raises if the cache is empty."""
    if not FIXTURE_DIR.exists():
        raise FileNotFoundError(
            "No fixtures cached. Run: python -m backend.bench.fixtures --force"
        )
    scenarios = [json.loads(p.read_text()) for p in sorted(FIXTURE_DIR.glob("*.json"))]
    if not scenarios:
        raise FileNotFoundError(
            "Fixture directory is empty. Run: python -m backend.bench.fixtures --force"
        )
    return scenarios


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    print("Fetching benchmark fixtures...")
    failed = refresh(force=force)
    if failed:
        print(f"Could not fetch: {', '.join(failed)}")
        sys.exit(1)
