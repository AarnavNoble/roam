"""
Builds a travel time matrix between POIs.
Uses OpenRouteService for accurate times, falls back to Haversine if rate limited.
"""

import httpx
import numpy as np
import math

from backend import config
from backend.core.resilience import CircuitBreaker, Deadline
from backend.core.telemetry import stage

ORS_URL = "https://api.openrouteservice.org/v2/matrix"

TRANSPORT_PROFILE = {
    "driving": "driving-car",
    "cycling": "cycling-regular",
    "walking": "foot-walking",
    "transit": "driving-car",  # ORS doesn't support transit; use driving as proxy
}

# Average speeds in km/h for Haversine fallback
SPEED_KMH = {
    "driving": 40,
    "cycling": 15,
    "walking": 5,
    "transit": 25,
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance in km between two coordinates."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def haversine_matrix(pois: list[dict], transport: str) -> np.ndarray:
    """Build NxN travel time matrix (seconds) using Haversine + avg speed."""
    speed = SPEED_KMH.get(transport, 30) / 3.6  # m/s
    lat = np.radians(np.array([p["lat"] for p in pois], dtype=np.float64))
    lon = np.radians(np.array([p["lon"] for p in pois], dtype=np.float64))
    dphi = lat[:, None] - lat[None, :]
    dlambda = lon[:, None] - lon[None, :]
    a = np.sin(dphi / 2) ** 2 + np.cos(lat[:, None]) * np.cos(lat[None, :]) * np.sin(dlambda / 2) ** 2
    dist_m = 6371000.0 * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return (dist_m / speed).astype(np.int32)


def ors_matrix(pois: list[dict], transport: str, api_key: str, timeout: float = 8.0) -> np.ndarray:
    """Build NxN travel time matrix (seconds) using OpenRouteService."""
    profile = TRANSPORT_PROFILE.get(transport, "driving-car")
    coords = [[p["lon"], p["lat"]] for p in pois]

    resp = httpx.post(
        f"{ORS_URL}/{profile}",
        json={"locations": coords, "metrics": ["duration"]},
        headers={"Authorization": api_key},
        timeout=timeout,
    )
    resp.raise_for_status()
    durations = resp.json()["durations"]
    return np.array(durations, dtype=np.int32)


_ors_breaker = CircuitBreaker("openrouteservice", failure_threshold=2, reset_after=120)


def build_time_matrix_with_source(pois: list[dict], transport: str,
                                  deadline: Deadline | None = None) -> tuple[np.ndarray, str]:
    """
    Returns (matrix, source). Real road times from ORS when a key is set and
    it answers; straight-line distance over an average speed otherwise.
    source is ors | haversine (no key, by design) | haversine_fallback (ORS failed).
    """
    api_key = config.ORS_API_KEY
    if not api_key or len(pois) > 50:  # ORS matrix limit
        return haversine_matrix(pois, transport), "haversine"
    try:
        timeout = deadline.timeout(8.0) if deadline else 8.0
        with stage("route.ors_matrix"):
            matrix = _ors_breaker.call(lambda: ors_matrix(pois, transport, api_key, timeout))
        return matrix, "ors"
    except Exception as e:
        print(f"ORS matrix failed ({e}), falling back to Haversine")
        return haversine_matrix(pois, transport), "haversine_fallback"


def build_time_matrix(pois: list[dict], transport: str) -> np.ndarray:
    return build_time_matrix_with_source(pois, transport)[0]
