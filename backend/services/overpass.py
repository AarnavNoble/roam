"""
Overpass API client for fetching POIs from OpenStreetMap.
Completely free, no API key needed.
"""

import httpx
from dataclasses import dataclass, field

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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
    "food":        ['amenity~"restaurant|cafe|bar|food_court|fast_food"'],
    "nature":      ['leisure~"park|nature_reserve|garden"', 'natural~"beach|waterfall|viewpoint"'],
    "history":     ['historic~"monument|castle|ruins|memorial|archaeological_site"'],
    "culture":     ['tourism~"museum|gallery|artwork"', 'amenity~"theatre|cinema"'],
    "nightlife":   ['amenity~"bar|nightclub|pub"'],
    "shopping":    ['shop~"mall|market|department_store|boutique"'],
    "adventure":   ['leisure~"climbing|sports_centre"', 'sport~"hiking|cycling"'],
    "hidden_gems": ['tourism~"attraction|viewpoint"', 'historic'],
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


def _build_query(lat: float, lon: float, radius_m: int, categories: list[str]) -> str:
    """Build Overpass QL query for given categories around a point."""
    tag_filters = []
    for cat in categories:
        for tag_expr in CATEGORY_QUERIES.get(cat, []):
            # [!"brand"] excludes chain restaurants/shops at the query level
            tag_filters.append(f'node[{tag_expr}][!"brand"](around:{radius_m},{lat},{lon});')
            tag_filters.append(f'way[{tag_expr}][!"brand"](around:{radius_m},{lat},{lon});')

    union = "\n".join(tag_filters)
    return f"""
[out:json][timeout:25];
(
{union}
);
out center 100;
"""


def fetch_pois(lat: float, lon: float, categories: list[str], radius_m: int = 5000) -> list[POI]:
    """
    Fetch POIs from Overpass API around a coordinate.
    Returns up to 100 POIs across the requested categories.
    """
    query = _build_query(lat, lon, radius_m, categories)
    resp = httpx.post(OVERPASS_URL, data={"data": query}, timeout=30)
    resp.raise_for_status()

    elements = resp.json().get("elements", [])
    pois = []

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        if _is_chain(name, tags):
            continue

        # get coordinates (nodes have lat/lon directly, ways have center)
        if el["type"] == "node":
            lat_el, lon_el = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            lat_el, lon_el = center.get("lat"), center.get("lon")

        if lat_el is None or lon_el is None:
            continue

        # infer category from tags
        category = _infer_category(tags)

        pois.append(POI(
            id=el["id"],
            name=name,
            lat=lat_el,
            lon=lon_el,
            category=category,
            tags=tags,
        ))

    return pois


def _infer_category(tags: dict) -> str:
    amenity = tags.get("amenity", "")
    tourism = tags.get("tourism", "")
    historic = tags.get("historic", "")
    leisure = tags.get("leisure", "")
    natural = tags.get("natural", "")

    if amenity in ("restaurant", "cafe", "bar", "food_court", "fast_food"):
        return "food"
    if amenity in ("nightclub", "pub"):
        return "nightlife"
    if tourism in ("museum", "gallery"):
        return "culture"
    if historic:
        return "history"
    if leisure in ("park", "nature_reserve", "garden") or natural:
        return "nature"
    return "attraction"
