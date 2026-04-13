"""
Nominatim geocoding client (OpenStreetMap).
Converts destination name to lat/lon. Completely free.
"""

import httpx
from functools import lru_cache

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "roam-app/1.0"}


@lru_cache(maxsize=512)
def geocode(destination: str) -> tuple[float, float]:
    """
    Returns (lat, lon) for a destination name.
    Raises ValueError if destination not found.
    """
    resp = httpx.get(
        NOMINATIM_URL,
        params={"q": destination, "format": "json", "limit": 1},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()

    if not results:
        raise ValueError(f"Could not geocode destination: {destination}")

    return float(results[0]["lat"]), float(results[0]["lon"])
