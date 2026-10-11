"""
Overpass API client for fetching POIs from OpenStreetMap.
Completely free, no API key needed.
"""

import hashlib
import re
import time
import httpx
from dataclasses import dataclass, field
from datetime import datetime

from backend.core.resilience import CircuitBreaker, Deadline, TTLCache, UpstreamError, hedged
from backend.core.telemetry import in_context, stage

# ── Opening-hours parser (OSM subset) ────────────────────────────────────────
# Handles the formats that cover >90% of OSM tags:
#   24/7 · Mo-Fr 09:00-18:00 · Sa,Su 10:00-16:00 · Tu off · semicolon rules

_DAY_MAP = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}


def _parse_oh_days(expr: str) -> set[int]:
    """'Mo-Fr' → {0..4}; 'Sa,Su' → {5,6}; 'Mo' → {0}"""
    days: set[int] = set()
    for part in re.split(r",\s*", expr.strip().lower()):
        m = re.match(r"^([a-z]{2})-([a-z]{2})$", part)
        if m:
            s, e = _DAY_MAP.get(m.group(1)), _DAY_MAP.get(m.group(2))
            if s is not None and e is not None:
                if s <= e:
                    days.update(range(s, e + 1))
                else:                        # wrap-around e.g. Sa-Mo
                    days.update(range(s, 7))
                    days.update(range(0, e + 1))
        else:
            d = _DAY_MAP.get(part.strip())
            if d is not None:
                days.add(d)
    return days


def _parse_oh_time(expr: str) -> tuple[float, float] | None:
    """'09:00-18:00' → (9.0, 18.0); returns None if unparseable."""
    m = re.match(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", expr.strip())
    if not m:
        return None
    sh, sm, eh, em = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    end = eh + em / 60 or 24.0   # 00:00 closing = midnight
    return sh + sm / 60, end


def _is_open_during(oh_str: str, weekday: int, trip_start: float, trip_end: float) -> bool:
    """
    Returns True if a place is open at any point in [trip_start, trip_end].
    weekday: 0=Mon … 6=Sun.
    Defaults to True when the tag is absent or unparseable (never over-filter).
    """
    if not oh_str:
        return True
    if oh_str.strip().lower() == "24/7":
        return True

    found_parseable = False

    for rule in oh_str.split(";"):
        rule = rule.strip()
        if not rule:
            continue

        # Split optional day prefix from time expression
        m = re.match(r"^([A-Za-z]{2}(?:[-,][A-Za-z]{2})*)\s+(.*)", rule)
        if m:
            day_expr, time_expr = m.group(1), m.group(2).strip()
            days = _parse_oh_days(day_expr)
            if not days or weekday not in days:
                continue
        else:
            time_expr = rule

        if time_expr.lower() == "off":
            found_parseable = True
            continue                         # explicitly closed — try next rule

        parsed = _parse_oh_time(time_expr)
        if parsed is None:
            continue                         # unrecognized format — skip rule

        found_parseable = True
        open_h, close_h = parsed
        if trip_start < close_h and trip_end > open_h:
            return True                      # trip window overlaps opening hours

    # If we found parseable rules but none matched → closed today
    # If we found no parseable rules → can't tell, assume open
    return not found_parseable

from backend import config
OVERPASS_MIRRORS = config.OVERPASS_MIRRORS
# overpass-api.de answers 406 to requests without an identifying User-Agent
HEADERS = {"User-Agent": "roam-app/1.0 (github.com/AarnavNoble/roam)"}

# OSM tags that map to travel interest categories
# Global chain brands to filter out — we want local places
CHAIN_BLOCKLIST = {
    "starbucks", "mcdonald's", "mcdonalds", "burger king", "kfc", "subway",
    "pizza hut", "domino's", "dominos", "dunkin", "dunkin'", "tim hortons",
    "costa coffee", "costa", "nero", "caffe nero", "pret", "pret a manger",
    "seven eleven", "7-eleven", "7eleven", "lawson", "familymart", "family mart",
    "circle k", "spar", "aldi", "lidl", "walmart", "tesco", "sainsbury's",
    "wendy's", "wendys", "taco bell", "popeyes", "chick-fil-a", "five guys",
    "shake shack", "mcdonald", "ikea", "zara", "h&m", "uniqlo",
}


def _is_chain(name: str, tags: dict = None) -> bool:
    if name.lower().strip() in CHAIN_BLOCKLIST:
        return True
    # OSM brand tag always has the English brand name even for non-English locations
    brand = (tags or {}).get("brand", "").lower().strip()
    return brand in CHAIN_BLOCKLIST


CATEGORY_QUERIES = {
    "food":        ['amenity~"restaurant|cafe|bar|fast_food|food_court"'],
    "nature":      ['leisure~"park|nature_reserve|garden"', 'natural~"beach|waterfall|peak"'],
    "history":     ['historic~"monument|castle|ruins|memorial|archaeological_site|fort"'],
    "culture":     ['tourism~"museum|gallery"', 'amenity~"theatre|arts_centre"'],
    "nightlife":   ['amenity~"bar|nightclub|pub|biergarten"'],
    "shopping":    ['shop~"mall|market|department_store|clothes|gift"'],
    "adventure":   ['tourism~"attraction|theme_park|zoo|aquarium"', 'leisure~"escape_game|miniature_golf|amusement_arcade"'],
    "hidden_gems": ['tourism~"attraction|viewpoint"'],
}


@dataclass
class POI:
    id: int
    name: str
    lat: float
    lon: float
    category: str
    tags: dict = field(default_factory=dict)

    @property
    def description(self) -> str:
        """Build a text description from OSM tags for embedding."""
        parts = [self.name, self.category]
        for key in ("description", "cuisine", "sport", "historic", "tourism", "amenity", "leisure"):
            val = self.tags.get(key)
            if val:
                parts.append(val.replace("_", " "))
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "category": self.category,
            "description": self.description,
            "tags": self.tags,
        }


PER_CATEGORY = 40        # candidates requested per category
FRESH_TTL_S = 3600       # serve from memory without touching Overpass
STALE_TTL_S = 7 * 86400  # how old a stored answer may be when every mirror is down
HEDGE_AFTER_S = 4.0      # start a second mirror if the first has not answered
MIRROR_TIMEOUT_S = 25.0

_memory_cache = TTLCache(ttl=FRESH_TTL_S, max_size=256)
_breakers: dict[str, CircuitBreaker] = {}


def _breaker(mirror: str) -> CircuitBreaker:
    if mirror not in _breakers:
        _breakers[mirror] = CircuitBreaker(mirror, failure_threshold=2, reset_after=60)
    return _breakers[mirror]


def _build_query(lat: float, lon: float, radius_m: int, categories: list[str],
                 per_category: int = PER_CATEGORY) -> str:
    """
    One Overpass request covering every category.

    Each category is collected into its own named set and printed with its own
    limit, so every category gets fair representation regardless of how dense
    its tags are, and the whole lookup costs one round trip instead of one per
    category.
    """
    blocks = []
    for i, category in enumerate(categories):
        filters = []
        for tag_expr in CATEGORY_QUERIES.get(category, []):
            filters.append(f'node[{tag_expr}][!"brand"](around:{radius_m},{lat},{lon});')
            filters.append(f'way[{tag_expr}][!"brand"](around:{radius_m},{lat},{lon});')
        if filters:
            blocks.append(f"({''.join(filters)})->.c{i};\n.c{i} out center {per_category};")
    return "[out:json][timeout:25];\n" + "\n".join(blocks)


def _post(mirror: str, query: str, timeout: float) -> list:
    resp = httpx.post(mirror, data={"data": query}, timeout=timeout, headers=HEADERS)
    resp.raise_for_status()
    return resp.json().get("elements", [])


def _query_overpass(query: str, deadline: Deadline | None = None) -> list:
    """
    Run a query against the mirrors and return the elements list.

    Mirrors are raced with a hedge delay: the first is called alone, and a
    second is started only if it is slow or fails. Mirrors whose breaker is
    open are skipped, so a dead mirror costs nothing after its first failures.
    """
    with stage("overpass.query"):
        mirrors = [m for m in OVERPASS_MIRRORS if _breaker(m).state != "open"] or list(OVERPASS_MIRRORS)
        timeout = deadline.timeout(MIRROR_TIMEOUT_S) if deadline else MIRROR_TIMEOUT_S
        calls = [
            in_context(lambda m=m: _breaker(m).call(lambda: _post(m, query, timeout)))
            for m in mirrors
        ]
        try:
            return hedged(calls, hedge_after=HEDGE_AFTER_S, timeout=timeout + HEDGE_AFTER_S * len(calls))
        except UpstreamError as e:
            raise UpstreamError(f"All Overpass mirrors failed: {e}") from e


def _cache_key(lat: float, lon: float, radius_m: int, categories: list[str]) -> str:
    # ~100m grid: nearby starts share an entry
    raw = f"{lat:.3f},{lon:.3f},{radius_m},{','.join(sorted(categories))},{PER_CATEGORY}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _store_durable(key: str, elements: list) -> None:
    try:
        from backend.db.models import PoiCache
        from backend.db.session import session_scope
        with session_scope() as db:
            db.merge(PoiCache(key=key, payload=elements, fetched_at=time.time()))
    except Exception as e:  # the cache is an optimisation; never fail a request over it
        print(f"POI cache write failed: {e}")


def _load_durable(key: str) -> list | None:
    try:
        from backend.db.models import PoiCache
        from backend.db.session import session_scope
        with session_scope() as db:
            row = db.get(PoiCache, key)
            if row and time.time() - row.fetched_at <= STALE_TTL_S:
                return row.payload
    except Exception as e:
        print(f"POI cache read failed: {e}")
    return None


def _fetch_elements(lat: float, lon: float, radius_m: int, categories: list[str],
                    deadline: Deadline | None) -> tuple[list, str]:
    """Return (elements, source) where source is live | cache | stale."""
    key = _cache_key(lat, lon, radius_m, categories)
    cached = _memory_cache.get(key)
    if cached is not None:
        return cached, "cache"
    try:
        elements = _query_overpass(_build_query(lat, lon, radius_m, categories), deadline)
    except UpstreamError:
        stale = _load_durable(key)
        if stale is None:
            raise
        return stale, "stale"
    if elements:
        _memory_cache.set(key, elements)
        _store_durable(key, elements)
    return elements, "live"


def fetch_pois_with_source(
    lat: float,
    lon: float,
    categories: list[str],
    radius_m: int = 2500,
    dietary: str = "none",
    visit_start_h: int = 9,
    visit_end_h: int = 23,
    weekday: int | None = None,
    deadline: Deadline | None = None,
) -> tuple[list[POI], str]:
    """
    Fetch POIs around a coordinate. Returns (pois, source), where source says
    whether the data is live, from the in-memory cache, or stale from the
    database because every mirror was down.

    dietary: filter food POIs by diet tag (none | vegetarian | vegan | halal | kosher)
    visit_start_h / visit_end_h: trip window in hours (0-24) used to filter
      places that are closed for the entire visit.
    weekday: 0=Mon…6=Sun, defaults to today if not provided.

    Raw Overpass elements are cached, not filtered POIs, because the dietary
    and opening-hours filters depend on the request.
    """
    if weekday is None:
        weekday = datetime.now().weekday()
    elements, source = _fetch_elements(lat, lon, radius_m, list(categories), deadline)

    seen_ids: set[int] = set()
    all_pois: list[POI] = []
    for el in elements:
        if el["id"] in seen_ids:
            continue

        tags = el.get("tags", {})
        name = tags.get("name")
        if not name or _is_chain(name, tags):
            continue
        # Skip OSM nodes that are clearly sub-elements (zoo animals, benches, etc.)
        if tags.get("species") or tags.get("animal") or tags.get("genus"):
            continue

        if el["type"] == "node":
            lat_el, lon_el = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            lat_el, lon_el = center.get("lat"), center.get("lon")

        if lat_el is None or lon_el is None:
            continue

        inferred = _infer_category(tags)

        if dietary != "none" and inferred == "food":
            diet_tag = tags.get(f"diet:{dietary}", "")
            if diet_tag not in ("yes", "only"):
                continue

        oh = tags.get("opening_hours", "")
        if not _is_open_during(oh, weekday, visit_start_h, visit_end_h):
            continue

        seen_ids.add(el["id"])
        all_pois.append(POI(
            id=el["id"], name=name,
            lat=lat_el, lon=lon_el,
            category=inferred, tags=tags,
        ))

    return all_pois, source


def fetch_pois(lat: float, lon: float, categories: list[str], radius_m: int = 2500,
               dietary: str = "none", visit_start_h: int = 9, visit_end_h: int = 23,
               weekday: int | None = None) -> list[POI]:
    """fetch_pois_with_source without the source, returning [] when Overpass is unreachable."""
    try:
        return fetch_pois_with_source(lat, lon, categories, radius_m, dietary,
                                      visit_start_h, visit_end_h, weekday)[0]
    except UpstreamError:
        return []


def _infer_category(tags: dict) -> str:
    amenity = tags.get("amenity", "")
    tourism = tags.get("tourism", "")
    historic = tags.get("historic", "")
    leisure = tags.get("leisure", "")
    natural = tags.get("natural", "")
    shop = tags.get("shop", "")

    if amenity in ("restaurant", "cafe", "food_court", "fast_food"):
        return "food"
    if amenity in ("bar", "nightclub", "pub", "biergarten"):
        return "nightlife"
    if tourism in ("museum", "gallery") or amenity in ("theatre", "arts_centre"):
        return "culture"
    if historic:
        return "history"
    if leisure in ("park", "nature_reserve", "garden") or natural:
        return "nature"
    if shop:
        return "shopping"
    return "attraction"
