"""
Benchmark fixtures: real POI sets fetched once from Overpass and cached to disk.

Caching matters for two reasons: Overpass is rate-limited and occasionally down,
and benchmark numbers are only comparable across runs if the inputs are identical.

Refresh the cache with:  python -m backend.bench.fixtures --refresh
"""

import json
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

MOBILITY_RADIUS_M = 2000


def _fixture_path(name: str) -> Path:
    return FIXTURE_DIR / f"{name}.json"


def fetch_scenario(name: str, city: str, start_location: str, goals: list[str],
                   duration_hours: int, transport: str) -> dict:
    """Fetch one scenario live from Nominatim + Overpass."""
    from backend.services.nominatim import geocode
    from backend.services.overpass import fetch_pois

    lat, lon = geocode(f"{start_location}, {city}")
    pois = fetch_pois(
        lat, lon, categories=goals, radius_m=MOBILITY_RADIUS_M,
        dietary="none", visit_start_h=9, visit_end_h=min(9 + duration_hours, 23),
        weekday=2,  # fixed weekday so opening-hours filtering is deterministic
    )
    return {
        "name": name,
        "city": city,
        "start_location": start_location,
        "goals": goals,
        "duration_hours": duration_hours,
        "transport": transport,
        "start_lat": lat,
        "start_lon": lon,
        "pois": [p.to_dict() for p in pois],
    }


def refresh(force: bool = False) -> None:
    """Fetch and cache every scenario. Skips ones already cached unless force."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, city, start, goals, hours, transport in SCENARIOS:
        path = _fixture_path(name)
        if path.exists() and not force:
            print(f"  {name}: cached, skipping")
            continue
        try:
            data = fetch_scenario(name, city, start, goals, hours, transport)
        except Exception as e:
            print(f"  {name}: FAILED ({type(e).__name__}: {e})")
            continue
        path.write_text(json.dumps(data, indent=2))
        print(f"  {name}: {len(data['pois'])} POIs cached")


def load_all() -> list[dict]:
    """Load every cached scenario. Raises if the cache is empty."""
    if not FIXTURE_DIR.exists():
        raise FileNotFoundError(
            "No fixtures cached. Run: python -m backend.bench.fixtures --refresh"
        )
    scenarios = [json.loads(p.read_text()) for p in sorted(FIXTURE_DIR.glob("*.json"))]
    if not scenarios:
        raise FileNotFoundError(
            "Fixture directory is empty. Run: python -m backend.bench.fixtures --refresh"
        )
    return scenarios


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    print("Fetching benchmark fixtures...")
    refresh(force=force)
