"""
Geocoding: place name to (lat, lon).

Primary is Nominatim (OpenStreetMap). When it is down or rate-limiting, the
same query goes to Photon, an independent geocoder built on the same OSM data,
so the coordinates agree closely. Results are cached, since the same handful
of neighbourhoods are requested over and over.
"""

import httpx

from backend.core.resilience import CircuitBreaker, Deadline, TTLCache, UpstreamError, retry
from backend.core.telemetry import stage

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
HEADERS = {"User-Agent": "roam-app/1.0 (github.com/AarnavNoble/roam)"}
TIMEOUT_S = 8.0

_cache = TTLCache(ttl=30 * 86400, max_size=2048)
_nominatim_breaker = CircuitBreaker("nominatim", failure_threshold=3, reset_after=60)
_photon_breaker = CircuitBreaker("photon", failure_threshold=3, reset_after=60)


class NotFound(ValueError):
    """The geocoder answered, and the place does not exist. Not retried."""


def _nominatim(query: str, timeout: float) -> tuple[float, float]:
    resp = httpx.get(NOMINATIM_URL, params={"q": query, "format": "json", "limit": 1},
                     headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise NotFound(f"Could not geocode destination: {query}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def _photon(query: str, timeout: float) -> tuple[float, float]:
    resp = httpx.get(PHOTON_URL, params={"q": query, "limit": 1}, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        raise NotFound(f"Could not geocode destination: {query}")
    lon, lat = features[0]["geometry"]["coordinates"]
    return float(lat), float(lon)


def geocode_with_source(destination: str, deadline: Deadline | None = None) -> tuple[float, float, str]:
    """
    Returns (lat, lon, source) where source is cache | nominatim | photon.
    Raises ValueError if the place does not exist, UpstreamError if no
    geocoder could be reached.
    """
    key = destination.strip().lower()
    cached = _cache.get(key)
    if cached is not None:
        return cached[0], cached[1], "cache"

    with stage("geocode.lookup"):
        last_error: Exception | None = None
        for source, breaker, lookup in (("nominatim", _nominatim_breaker, _nominatim),
                                        ("photon", _photon_breaker, _photon)):
            timeout = deadline.timeout(TIMEOUT_S) if deadline else TIMEOUT_S
            try:
                lat, lon = retry(
                    lambda: breaker.call(lambda: lookup(destination, timeout)),
                    attempts=2, retry_on=(httpx.HTTPError,), deadline=deadline,
                )
            except NotFound:
                raise
            except Exception as e:
                last_error = e
                continue
            _cache.set(key, (lat, lon))
            return lat, lon, source
        raise UpstreamError(f"Geocoding unavailable: {last_error}")


def geocode(destination: str) -> tuple[float, float]:
    lat, lon, _ = geocode_with_source(destination)
    return lat, lon
