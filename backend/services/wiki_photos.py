"""
Photo fetching from Wikimedia/Wikipedia using OSM tags.
Uses two sources in order:
  1. 'wikipedia' tag  → Wikipedia REST page summary (has thumbnail)
  2. 'wikidata' tag   → Wikidata P18 image claim → Commons Special:FilePath
No API key required. Returns None when no photo is available.
"""

import re
import httpx
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

_HEADERS = {"User-Agent": "roam-travel-app/1.0 (open-source travel planner)"}
_TIMEOUT = 4  # seconds per request — keep it short so slow lookups don't stall the pipeline


def _fetch_wikipedia_photo(tag: str) -> str | None:
    """
    tag: 'en:Eiffel Tower' or 'Eiffel Tower'
    Calls the Wikipedia REST summary endpoint and returns the thumbnail URL.
    """
    lang, title = tag.split(":", 1) if ":" in tag else ("en", tag)
    encoded = quote(title.strip().replace(" ", "_"))
    try:
        resp = httpx.get(
            f"https://{lang.strip()}.wikipedia.org/api/rest_v1/page/summary/{encoded}",
            timeout=_TIMEOUT,
            headers=_HEADERS,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            src = resp.json().get("thumbnail", {}).get("source", "")
            if src:
                # Bump to 500px width for a sharper banner
                return re.sub(r"/\d+px-", "/500px-", src)
    except Exception:
        pass
    return None


def _fetch_wikidata_photo(qid: str) -> str | None:
    """
    qid: 'Q12345'
    Fetches the P18 (image) claim from Wikidata and builds a Commons thumbnail URL.
    """
    try:
        resp = httpx.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetclaims", "entity": qid, "property": "P18", "format": "json"},
            timeout=_TIMEOUT,
            headers=_HEADERS,
        )
        if resp.status_code == 200:
            claims = resp.json().get("claims", {}).get("P18", [])
            if claims:
                filename = claims[0]["mainsnak"]["datavalue"]["value"].replace(" ", "_")
                return (
                    f"https://commons.wikimedia.org/wiki/Special:FilePath/"
                    f"{quote(filename)}?width=500"
                )
    except Exception:
        pass
    return None


def fetch_photo_url(tags: dict) -> str | None:
    """
    Try wikipedia tag first, then wikidata. Returns None if neither yields a photo.
    """
    wikipedia = tags.get("wikipedia", "")
    if wikipedia:
        url = _fetch_wikipedia_photo(wikipedia)
        if url:
            return url

    wikidata = tags.get("wikidata", "")
    if wikidata:
        return _fetch_wikidata_photo(wikidata)

    return None


def fetch_photos_for_itinerary(itinerary: list[dict]) -> dict[str, str]:
    """
    Fetch photos for all stops in an itinerary in parallel.
    Returns {stop_name: photo_url} for stops that have a photo.
    """
    stops = [
        (poi["name"], poi.get("tags", {}))
        for day in itinerary
        for poi in day.get("pois", [])
    ]

    def _one(name_tags: tuple[str, dict]) -> tuple[str, str | None]:
        name, tags = name_tags
        return name, fetch_photo_url(tags)

    photo_map: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, url in pool.map(_one, stops):
            if url:
                photo_map[name] = url

    return photo_map
