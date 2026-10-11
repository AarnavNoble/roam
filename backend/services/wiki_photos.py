"""
Photo fetching from Wikimedia/Wikipedia using OSM tags.
Uses two sources in order:
  1. 'wikipedia' tag  → Wikipedia REST page summary (has thumbnail)
  2. 'wikidata' tag   → Wikidata P18 image claim → Commons Special:FilePath
No API key required. Returns None when no photo is available.

"No photo" and "could not look" are different: a 404 means the page has no
image, while a timeout or a 5xx means the service failed. The second kind is
raised as PhotoLookupError so the pipeline can report photos:unavailable.
"""

import re
import httpx
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

from backend.core.resilience import UpstreamError

_HEADERS = {"User-Agent": "roam-travel-app/1.0 (open-source travel planner)"}
_TIMEOUT = 4  # seconds per request — keep it short so slow lookups don't stall the pipeline


class PhotoLookupError(UpstreamError):
    """Wikipedia or Wikidata could not be reached, or answered with a server error."""


def _get(url: str, **kwargs) -> httpx.Response | None:
    """The response on 200, None on a plain miss (404 etc), PhotoLookupError if the service failed."""
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, headers=_HEADERS, **kwargs)
    except httpx.HTTPError as e:
        raise PhotoLookupError(f"{type(e).__name__}: {url}") from e
    if resp.status_code >= 500 or resp.status_code == 429:
        raise PhotoLookupError(f"HTTP {resp.status_code}: {url}")
    return resp if resp.status_code == 200 else None


def _fetch_wikipedia_photo(tag: str) -> str | None:
    """
    tag: 'en:Eiffel Tower' or 'Eiffel Tower'
    Calls the Wikipedia REST summary endpoint and returns the thumbnail URL.
    """
    lang, title = tag.split(":", 1) if ":" in tag else ("en", tag)
    encoded = quote(title.strip().replace(" ", "_"))
    resp = _get(f"https://{lang.strip()}.wikipedia.org/api/rest_v1/page/summary/{encoded}",
                follow_redirects=True)
    try:
        if resp is not None:
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
    resp = _get("https://www.wikidata.org/w/api.php",
                params={"action": "wbgetclaims", "entity": qid, "property": "P18", "format": "json"})
    try:
        if resp is not None:
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
    Raises PhotoLookupError if no photo was found and a lookup failed along the way.
    """
    failure: PhotoLookupError | None = None
    for tag, lookup in ((tags.get("wikipedia", ""), _fetch_wikipedia_photo),
                        (tags.get("wikidata", ""), _fetch_wikidata_photo)):
        if not tag:
            continue
        try:
            url = lookup(tag)
        except PhotoLookupError as e:
            failure = e
            continue
        if url:
            return url
    if failure:
        raise failure
    return None


def fetch_photos_for_itinerary(itinerary: list[dict]) -> dict[str, str]:
    """
    Fetch photos for all stops in an itinerary in parallel.
    Returns {stop_name: photo_url} for stops that have a photo.
    Raises UpstreamError if lookups failed and not one photo could be fetched,
    so "the photo services are down" is not mistaken for "no stop has a photo".
    """
    stops = [
        (poi["name"], poi.get("tags", {}))
        for day in itinerary
        for poi in day.get("pois", [])
    ]

    def _one(name_tags: tuple[str, dict]) -> tuple[str, str | PhotoLookupError | None]:
        name, tags = name_tags
        try:
            return name, fetch_photo_url(tags)
        except PhotoLookupError as e:
            return name, e

    photo_map: dict[str, str] = {}
    failures = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, url in pool.map(_one, stops):
            if isinstance(url, PhotoLookupError):
                failures += 1
            elif url:
                photo_map[name] = url

    if failures and not photo_map:
        raise UpstreamError(f"photo lookup failed for all {failures} stops that have one")
    return photo_map
