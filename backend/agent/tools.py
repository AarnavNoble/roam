"""
The tools the editing agent can call, and the state they act on.

An EditSession holds a working copy of one trip's stops. The model never sees
or returns coordinates, tags or OSM ids: it refers to stops as s1, s2... and to
search results as p1, p2..., and every tool looks those up in the session. That
is what makes the guardrails structural rather than a matter of prompting:

  * a place can only be added if a search in this session returned it, so the
    model cannot invent one;
  * the trip can never be left with zero stops, and an edit that tries to
    empty it is rejected as a whole;
  * the number of stops is capped. Time is not checked here: the re-route
    makes the trip fit its hours and reports what it had to drop (service.py);
  * arguments are validated here, and a bad call comes back to the model as an
    "Error: ..." string. `call` never raises, so nothing the model sends can
    break the loop.
"""

import json
from dataclasses import dataclass, field
from typing import get_args

from backend.core.resilience import Deadline, UpstreamError
from backend.ml.ranker.scorer import rank_pois
from backend.pipeline import (
    MOBILITY_RADIUS, PACE_STOPS_PER_DAY, START_HOUR, Goal, n_days, trip_weekday,
)
from backend.services.nominatim import geocode_with_source
from backend.services.overpass import fetch_pois_with_source

CATEGORIES = list(get_args(Goal))
DIETS = ("vegetarian", "vegan", "halal", "kosher")
SEARCH_RESULTS = 5
MAX_STOPS_PER_DAY = PACE_STOPS_PER_DAY["packed"]
# Tags that tell the model what kind of place a stop is. The rest is noise.
KEY_TAGS = ("amenity", "cuisine", "tourism", "historic", "leisure", "natural", "shop")


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_STOP_ID = {"type": "string", "description": "A stop id from the trip, like s2"}
_PLACE_ID = {"type": "string", "description": "A place id from search_places results, like p1"}

TOOLS = [
    _fn("list_stops", "List the trip's current stops in visit order.", {}, []),
    _fn("remove_stop", "Remove one stop from the trip.", {"stop_id": _STOP_ID}, ["stop_id"]),
    _fn("search_places",
        "Find places near the trip's start that are not in the trip. "
        "Returns up to 5 candidates with place ids.",
        {"category": {"type": "string", "enum": CATEGORIES,
                      "description": "food=restaurants/cafes, nature=parks/gardens/beaches, "
                                     "history=monuments/ruins, culture=museums/galleries/theatres, "
                                     "nightlife=bars/pubs/clubs, shopping=shops/markets, "
                                     "adventure=attractions/zoos, hidden_gems=viewpoints"},
         "query": {"type": "string",
                   "description": "The specific kind of place the traveller asked for, in a few words, "
                                  "e.g. 'museum', 'vegetarian restaurant', 'quiet park'. Results are ranked by it."}},
        ["category", "query"]),
    _fn("add_stop", "Add a place from search results to the trip.", {"place_id": _PLACE_ID}, ["place_id"]),
    _fn("replace_stop", "Swap one stop for a place from search results.",
        {"stop_id": _STOP_ID, "place_id": _PLACE_ID}, ["stop_id", "place_id"]),
    _fn("finish", "End the edit. Call this last, or straight away if nothing should change.",
        {"summary": {"type": "string", "description": "One sentence for the traveller on what was done or why not"}},
        ["summary"]),
]


class ToolError(Exception):
    """A tool call the model got wrong. Its message is sent back to the model."""


def _clean(text: object, limit: int) -> str:
    return " ".join(str(text).split())[:limit]


def _key_tags(poi: dict) -> str:
    tags = poi.get("tags", {})
    parts = [f"{k}={_clean(tags[k], 40)}" for k in KEY_TAGS if tags.get(k)]
    parts += [k for k in sorted(tags) if k.startswith("diet:") and tags[k] in ("yes", "only")]
    return "; ".join(parts)


def _line(ref: str, poi: dict, when: str = "") -> str:
    head = f"{ref} {when} {_clean(poi['name'], 60)}".replace("  ", " ")
    return f"{head} ({poi.get('category', 'attraction')}) {_key_tags(poi)}".rstrip()


@dataclass
class EditSession:
    request: dict                                               # the stored TripRequest
    stops: dict[str, dict] = field(default_factory=dict)        # "s1" -> POI, in visit order
    candidates: dict[str, dict] = field(default_factory=dict)   # "p1" -> POI, from searches
    changes: list[str] = field(default_factory=list)
    deadline: Deadline | None = None
    finished: bool = False
    summary: str = ""
    rejected: bool = False        # the edit tried to empty the trip
    trimmed: list[dict] = field(default_factory=list)   # stops the re-route dropped for time
    _original_ids: list = field(default_factory=list)
    _next_stop: int = 1
    _start: tuple[float, float] | None = None

    @classmethod
    def from_trip(cls, request: dict, route: list[dict], itinerary: dict | None = None,
                  deadline: Deadline | None = None) -> "EditSession":
        """
        Working copy of a stored trip. Stops the writer excluded (because the
        traveller's notes ruled them out) are in the route but not in the
        itinerary, so they are left out here too: the agent edits what the
        traveller can see.
        """
        shown = {s.get("id") for d in (itinerary or {}).get("days", []) for s in d.get("stops", [])}
        session = cls(request=request, deadline=deadline)
        for day in route:
            for poi in day.get("pois", []):
                if not shown or poi.get("id") in shown:
                    session._append(dict(poi))
        session._original_ids = [p.get("id") for p in session.stops.values()]
        return session

    # ── state ────────────────────────────────────────────────────────────────

    def _append(self, poi: dict) -> str:
        sid = f"s{self._next_stop}"
        self._next_stop += 1
        self.stops[sid] = poi
        return sid

    @property
    def changed(self) -> bool:
        """True if the set of places differs from the stored trip."""
        if self.rejected:
            return False
        return sorted(map(str, self._original_ids)) != sorted(str(p.get("id")) for p in self.stops.values())

    @property
    def n_days(self) -> int:
        return n_days(self.request.get("duration_hours", 6))

    @property
    def max_stops(self) -> int:
        return max(MAX_STOPS_PER_DAY * self.n_days, len(self._original_ids))

    def start(self) -> tuple[float, float]:
        """Coordinates of the trip's start. The stored request only has the name."""
        if self._start is None:
            lat, lon, _ = geocode_with_source(
                f"{self.request['start_location']}, {self.request['city']}", self.deadline)
            self._start = (lat, lon)
        return self._start

    def describe_stops(self) -> str:
        return "\n".join(_line(sid, poi, poi.get("arrival_time", "new")) for sid, poi in self.stops.items())

    # ── argument validation ──────────────────────────────────────────────────

    def _stop_arg(self, args: dict) -> str:
        sid = args.get("stop_id")
        if not isinstance(sid, str) or sid.strip().lower() not in self.stops:
            raise ToolError(f"stop_id must be one of: {', '.join(self.stops)}")
        return sid.strip().lower()

    def _place_arg(self, args: dict) -> dict:
        pid = args.get("place_id")
        if not isinstance(pid, str) or pid.strip().lower() not in self.candidates:
            known = ", ".join(self.candidates) or "none yet"
            raise ToolError(f"place_id must come from search_places results in this edit ({known}). "
                            "Call search_places first.")
        poi = self.candidates[pid.strip().lower()]
        if any(p.get("id") == poi.get("id") for p in self.stops.values()):
            raise ToolError(f"{poi['name']} is already in the trip")
        return poi

    # ── tools ────────────────────────────────────────────────────────────────

    def list_stops(self, args: dict) -> str:
        return self.describe_stops()

    def remove_stop(self, args: dict) -> str:
        sid = self._stop_arg(args)
        if len(self.stops) == 1:
            self.rejected = True
            raise ToolError("a trip needs at least one stop, so the last one cannot be removed")
        poi = self.stops.pop(sid)
        self.changes.append(f"Removed {poi['name']}")
        return f"Removed {sid} {_clean(poi['name'], 60)}. {len(self.stops)} stops left."

    def search_places(self, args: dict) -> str:
        category = args.get("category")
        if category not in CATEGORIES:
            raise ToolError(f"category must be one of: {', '.join(CATEGORIES)}")
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query is required: the kind of place wanted, e.g. 'museum'")
        query = _clean(query, 120)

        # Similarity ranking cannot guarantee a diet, so a diet word in the
        # query becomes the same hard tag filter the pipeline applies.
        diet = next((d for d in DIETS if d in query.lower()), self.request.get("dietary", "none"))
        start_h = START_HOUR.get(self.request.get("start_time", "morning"), 9)
        try:
            lat, lon = self.start()
            pois, _ = fetch_pois_with_source(
                lat, lon, categories=[category],
                radius_m=MOBILITY_RADIUS.get(self.request.get("mobility", "moderate"), 2000),
                dietary=diet, visit_start_h=start_h,
                visit_end_h=min(start_h + self.request.get("duration_hours", 6), 23),
                weekday=trip_weekday(self.request.get("trip_date", "")), deadline=self.deadline,
            )
        except UpstreamError:
            raise ToolError("place search is unavailable right now")

        # a stop removed in this edit is not offered straight back
        in_trip = {p.get("id") for p in self.stops.values()} | set(self._original_ids)
        fresh = [p.to_dict() for p in pois if p.id not in in_trip]
        if not fresh:
            return f"No places found for category={category}" + (f" with diet={diet}" if diet != "none" else "")
        # One combined goal string: the ranker caps results per category when given several goals.
        ranked = rank_pois([f"{category} {query}"], fresh, top_k=SEARCH_RESULTS)

        by_osm_id = {poi.get("id"): pid for pid, poi in self.candidates.items()}
        lines = []
        for poi in ranked:
            pid = by_osm_id.get(poi.get("id"))
            if pid is None:
                pid = f"p{len(self.candidates) + 1}"
                self.candidates[pid] = poi
            lines.append(_line(pid, poi))
        return "\n".join(lines)

    def add_stop(self, args: dict) -> str:
        poi = self._place_arg(args)
        if len(self.stops) >= self.max_stops:
            raise ToolError(f"the trip already has {len(self.stops)} stops, which is the maximum. "
                            "Use replace_stop or remove a stop first.")
        sid = self._append(dict(poi))
        self.changes.append(f"Added {poi['name']}")
        return f"Added {_clean(poi['name'], 60)} as {sid}. {len(self.stops)} stops now."

    def replace_stop(self, args: dict) -> str:
        sid = self._stop_arg(args)
        poi = self._place_arg(args)
        old = self.stops[sid]
        new_sid = f"s{self._next_stop}"
        self._next_stop += 1
        # rebuild so the new stop takes the old one's position
        self.stops = {(new_sid if k == sid else k): (dict(poi) if k == sid else v)
                      for k, v in self.stops.items()}
        self.changes.append(f"Replaced {old['name']} with {poi['name']}")
        return f"Replaced {sid} {_clean(old['name'], 60)} with {_clean(poi['name'], 60)} (now {new_sid})."

    def finish(self, args: dict) -> str:
        summary = args.get("summary", "")
        if not isinstance(summary, str):
            raise ToolError("summary must be a string")
        self.finished = True
        self.summary = _clean(summary, 300)
        return "Done."

    def call(self, name: str, raw_arguments: str | None) -> str:
        """Run one tool call from the model. Always returns a string, never raises."""
        tool = TOOL_FUNCTIONS.get(name)
        if tool is None:
            return f"Error: unknown tool {_clean(name, 40)!r}. Tools: {', '.join(TOOL_FUNCTIONS)}"
        try:
            args = json.loads(raw_arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            return "Error: arguments were not valid JSON"
        if not isinstance(args, dict):
            return "Error: arguments must be a JSON object"
        try:
            return tool(self, args)
        except ToolError as e:
            return f"Error: {e}"
        except Exception as e:  # a bug in a tool must not take the request down with it
            print(f"Agent tool {name} failed: {type(e).__name__}: {e}")
            return "Error: the tool failed, try something else or finish"


TOOL_FUNCTIONS = {
    "list_stops": EditSession.list_stops,
    "remove_stop": EditSession.remove_stop,
    "search_places": EditSession.search_places,
    "add_stop": EditSession.add_stop,
    "replace_stop": EditSession.replace_stop,
    "finish": EditSession.finish,
}
