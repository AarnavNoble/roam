"""
Builds a travel time matrix between POIs.
Uses OpenRouteService for accurate times, falls back to Haversine if rate limited.
"""

import httpx
import numpy as np
import math
import os

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
    n = len(pois)
    speed = SPEED_KMH.get(transport, 30) / 3.6  # m/s
    matrix = np.zeros((n, n), dtype=np.int32)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            dist_km = haversine_km(pois[i]["lat"], pois[i]["lon"], pois[j]["lat"], pois[j]["lon"])
            matrix[i][j] = int((dist_km * 1000) / speed)
    return matrix


def ors_matrix(pois: list[dict], transport: str, api_key: str) -> np.ndarray:
    """Build NxN travel time matrix (seconds) using OpenRouteService."""
    profile = TRANSPORT_PROFILE.get(transport, "driving-car")
    coords = [[p["lon"], p["lat"]] for p in pois]

    resp = httpx.post(
        f"{ORS_URL}/{profile}",
        json={"locations": coords, "metrics": ["duration"]},
        headers={"Authorization": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    durations = resp.json()["durations"]
    return np.array(durations, dtype=np.int32)


def build_time_matrix(pois: list[dict], transport: str) -> np.ndarray:
    """
    Build travel time matrix. Uses ORS if API key is set, else Haversine fallback.
    """
    api_key = os.getenv("ORS_API_KEY", "")
    if api_key and len(pois) <= 50:  # ORS matrix limit
        try:
            return ors_matrix(pois, transport, api_key)
        except Exception as e:
            print(f"ORS matrix failed ({e}), falling back to Haversine")
    return haversine_matrix(pois, transport)
