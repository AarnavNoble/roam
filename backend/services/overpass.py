"""
Overpass API client for fetching POIs from OpenStreetMap.
Completely free, no API key needed.
"""

import httpx
from dataclasses import dataclass, field

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

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


def _build_category_query(lat: float, lon: float, radius_m: int, category: str) -> str:
    """Build a query for a single category, capped at 15 results."""
    tag_filters = []
    for tag_expr in CATEGORY_QUERIES.get(category, []):
        tag_filters.append(f'node[{tag_expr}][!"brand"](around:{radius_m},{lat},{lon});')
        tag_filters.append(f'way[{tag_expr}][!"brand"](around:{radius_m},{lat},{lon});')
    union = "\n".join(tag_filters)
    return f"""
[out:json][timeout:25];
(
{union}
);
out center 15;
"""


def _query_overpass(query: str) -> list:
    """Execute a query against Overpass mirrors, return elements list."""
    last_error = None
    for mirror in OVERPASS_MIRRORS:
        try:
            resp = httpx.post(mirror, data={"data": query}, timeout=30)
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"All Overpass mirrors failed: {last_error}")


def fetch_pois(lat: float, lon: float, categories: list[str], radius_m: int = 2500, dietary: str = "none") -> list[POI]:
    """
    Fetch POIs from Overpass API around a coordinate.
    Queries each category separately (15 results each) so every category
    gets fair representation regardless of OSM node density.
    dietary: filter food POIs by diet tag (none | vegetarian | vegan | halal | kosher)
    """
    seen_ids: set[int] = set()
    all_pois: list[POI] = []

    for category in categories:
        query = _build_category_query(lat, lon, radius_m, category)
        try:
            elements = _query_overpass(query)
        except RuntimeError:
            continue  # skip category if mirrors fail, don't abort entire request

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

            seen_ids.add(el["id"])
            all_pois.append(POI(
                id=el["id"], name=name,
                lat=lat_el, lon=lon_el,
                category=inferred, tags=tags,
            ))

    return all_pois




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
