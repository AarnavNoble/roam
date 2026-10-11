"""
Turns an optimised route into the itinerary a traveller reads.

The route (which stops, in what order, at what time, at which coordinates) is
decided upstream by the ranker and the solver. The LLM's job is only to write
about it. So the model is never asked to repeat facts: it gets a numbered list
of stops and returns prose keyed by stop number, and the server joins that
prose onto the facts it already holds. Names, coordinates and times cannot be
hallucinated because they never pass through the model's output.

That also made the prompt and the completion several times smaller than the
first version, which had the model echo every stop as a full JSON object.

  build_prompt   route + traveller profile + retrieved context -> prompt
  validate       what is wrong with a raw model answer, as a list of violations
  assemble       join prose onto the route, repairing anything invalid
  fallback_raw   the same shape of answer, written without a model
"""

from dataclasses import dataclass

PACE = {"relaxed": "fewer stops, more time to linger", "moderate": "balanced pace",
        "packed": "maximize stops, tight schedule"}
BUDGET = {"free": "only free activities", "budget": "cheap eats and free attractions",
          "mid": "mix of free and paid", "splurge": "premium experiences welcome"}
STYLE = {"solo": "solo traveler", "couple": "traveling as a couple",
         "family": "family with kids", "group": "group of friends"}
FAMILIARITY = {"first_time": "first time visitor",
               "returning": "returning visitor who wants to go beyond the obvious"}

# Tags worth showing the model. Everything else in an OSM tag set is noise.
PROMPT_TAGS = ("cuisine", "historic", "tourism", "amenity", "leisure", "natural", "shop",
               "description", "opening_hours", "heritage", "artwork_type")
MAX_CONTEXT_CHARS = 2400


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str


def flatten(route: list[dict]) -> list[dict]:
    """Number every stop 1..N across days. Returns [{sid, day, poi, travel_to_next_min}]."""
    stops = []
    for day in route:
        travel = day.get("travel_times_sec", [])
        for i, poi in enumerate(day["pois"]):
            stops.append({
                "sid": len(stops) + 1, "day": day["day"], "poi": poi,
                "travel_to_next_min": round(travel[i] / 60) if i < len(travel) else None,
            })
    return stops


def _stop_line(stop: dict) -> str:
    poi = stop["poi"]
    tags = poi.get("tags", {})
    extras = [f"{k}={str(tags[k])[:60]}" for k in PROMPT_TAGS if tags.get(k)]
    line = (f"[{stop['sid']}] {poi.get('arrival_time', '')} {poi['name']} "
            f"({poi.get('category', 'attraction')}, {poi.get('visit_duration_min', 45)} min)")
    if extras:
        line += " " + "; ".join(extras)
    if stop["travel_to_next_min"] is not None:
        line += f" -> {stop['travel_to_next_min']} min to next"
    return line


def build_prompt(trip: dict, route: list[dict], rag_context: str) -> str:
    stops = flatten(route)
    lines = []
    for day in route:
        lines.append(f"Day {day['day']}:")
        lines.extend(_stop_line(s) for s in stops if s["day"] == day["day"])
    notes = (trip.get("notes") or "").strip()
    dietary = trip.get("dietary", "none")
    transport = "public transit" if trip.get("transport") == "transit" else "walking"

    profile = [
        f"- Starting from: {trip.get('start_location', '')}, {trip.get('city', '')}",
        f"- Time available: {trip.get('duration_hours', 6)} hours, getting around by {transport}",
        f"- Interests: {', '.join(trip.get('goals', []))}",
        f"- Pace: {PACE.get(trip.get('pace', 'moderate'), 'balanced pace')}",
        f"- Budget: {BUDGET.get(trip.get('budget', 'mid'), 'mix of free and paid')}",
        f"- Traveling as: {STYLE.get(trip.get('style', 'solo'), 'solo traveler')}",
        f"- Visitor type: {FAMILIARITY.get(trip.get('familiarity', 'first_time'), 'first time visitor')}",
    ]
    if dietary != "none":
        profile.append(f"- Dietary: {dietary}")

    if notes:
        notes_block = (
            f'\nTRAVELER\'S NOTES: "{notes}"\n'
            '- If the notes EXPLICITLY rule something out ("no museums", "hate bars"), list the '
            'numbers of the stops they rule out in "excluded". Otherwise "excluded" stays empty.\n'
            "- A positive preference is a reason to write warmly about matching stops, never a "
            "reason to exclude the others.\n"
        )
    else:
        notes_block = '\n"excluded" must be an empty list.\n'

    return f"""You are a local expert writing up a day out in {trip.get('city', '')} for one traveler.

TRAVELER PROFILE:
{chr(10).join(profile)}
{notes_block}
LOCAL KNOWLEDGE (use it where it is relevant to a stop):
{rag_context[:MAX_CONTEXT_CHARS]}

ROUTE (already chosen, ordered and timed; you do not change it):
{chr(10).join(lines)}

Write about every numbered stop. Refer to each stop only by its number.
- description: what to do there and why it suits this traveler, 1-2 sentences
- tip: one practical tip
- For outdoor stops describe the experience, not just "it's a park"
- Do not mention any place that is not in the route

Return JSON exactly in this shape:
{{
  "overview": "2-3 sentences, like a friend describing a great day out",
  "days": [{{"day": 1, "theme": "short evocative theme", "summary": "one sentence"}}],
  "stops": [{{"id": 1, "description": "...", "tip": "..."}}],
  "excluded": []
}}"""


def _entries(raw: dict) -> list:
    stops = raw.get("stops")
    return stops if isinstance(stops, list) else []


def validate(raw: dict, route: list[dict], allow_exclusions: bool) -> list[Violation]:
    """Everything wrong with a raw model answer. Empty list means it conforms."""
    violations: list[Violation] = []
    expected = {s["sid"] for s in flatten(route)}
    if not isinstance(raw.get("stops"), list):
        return [Violation("bad_shape", '"stops" is missing or not a list')]

    seen: set[int] = set()
    for entry in _entries(raw):
        sid = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(sid, int) or isinstance(sid, bool):
            violations.append(Violation("bad_shape", f"stop entry without an integer id: {str(entry)[:60]}"))
            continue
        if sid not in expected:
            violations.append(Violation("unknown_stop", f"id {sid} is not in the route"))
            continue
        if sid in seen:
            violations.append(Violation("duplicate_stop", f"id {sid} described twice"))
            continue
        seen.add(sid)
        if not str(entry.get("description") or "").strip():
            violations.append(Violation("empty_description", f"id {sid} has no description"))

    excluded = raw.get("excluded") or []
    if not isinstance(excluded, list):
        violations.append(Violation("bad_shape", '"excluded" is not a list'))
        excluded = []
    valid_excluded = {e for e in excluded if isinstance(e, int) and e in expected}
    if excluded and not allow_exclusions:
        violations.append(Violation("illegal_exclusion", "stops excluded although the traveler gave no notes"))
        valid_excluded = set()
    elif len(valid_excluded) > len(expected) // 2:
        violations.append(Violation("illegal_exclusion", f"{len(valid_excluded)} of {len(expected)} stops excluded"))
        valid_excluded = set()

    for sid in sorted(expected - seen - valid_excluded):
        violations.append(Violation("missing_stop", f"id {sid} was not described"))
    if not str(raw.get("overview") or "").strip():
        violations.append(Violation("missing_overview", "no overview"))
    return violations


def _describe(poi: dict) -> tuple[str, str]:
    """Plain description and tip for a stop, from its tags alone."""
    tags = poi.get("tags", {})
    category = poi.get("category", "attraction")
    kind = (tags.get("cuisine") or tags.get("historic") or tags.get("tourism")
            or tags.get("leisure") or tags.get("amenity") or category)
    kind = str(kind).split(";")[0].replace("_", " ")
    minutes = poi.get("visit_duration_min", 45)
    description = tags.get("description") or f"{poi['name']} is a {kind} stop picked for your {category} interest. Plan about {minutes} minutes here."
    hours = tags.get("opening_hours")
    tip = f"Opening hours: {hours}." if hours else "Check opening hours before you go."
    return description, tip


def fallback_raw(trip: dict, route: list[dict]) -> dict:
    """An answer in the model's output shape, written without a model."""
    stops = flatten(route)
    goals = ", ".join(trip.get("goals", [])) or "the city"
    days = []
    for day in route:
        cats = sorted({p.get("category", "attraction") for p in day["pois"]})
        days.append({"day": day["day"], "theme": " and ".join(cats[:2]).title() or "Exploring",
                     "summary": f"{len(day['pois'])} stops, about {day.get('total_travel_min', 0)} minutes of travel."})
    return {
        "overview": (f"A {trip.get('duration_hours', 6)}-hour route through {trip.get('city', '')} "
                     f"built around {goals}, starting from {trip.get('start_location', '')}. "
                     f"Stops are ordered to keep travel short."),
        "days": days,
        "stops": [dict(zip(("description", "tip"), _describe(s["poi"])), id=s["sid"]) for s in stops],
        "excluded": [],
    }


def assemble(raw: dict, route: list[dict], trip: dict) -> tuple[dict, list[Violation]]:
    """
    Join the model's prose onto the route. Always returns a complete, valid
    itinerary: any stop the model skipped or mangled gets the deterministic
    description, and exclusions that are not allowed are ignored.
    Returns (itinerary, violations found in raw).
    """
    allow_exclusions = bool((trip.get("notes") or "").strip())
    violations = validate(raw, route, allow_exclusions)
    stops = flatten(route)

    prose: dict[int, dict] = {}
    for entry in _entries(raw):
        if isinstance(entry, dict) and isinstance(entry.get("id"), int) and entry["id"] not in prose:
            prose[entry["id"]] = entry

    excluded: set[int] = set()
    if allow_exclusions and not any(v.code == "illegal_exclusion" for v in violations):
        valid_ids = {s["sid"] for s in stops}
        excluded = {e for e in (raw.get("excluded") or []) if isinstance(e, int) and e in valid_ids}

    day_meta = {d.get("day"): d for d in (raw.get("days") or []) if isinstance(d, dict)}
    fallback = fallback_raw(trip, route)
    fallback_days = {d["day"]: d for d in fallback["days"]}

    days = []
    for day in route:
        out_stops = []
        for s in (s for s in stops if s["day"] == day["day"]):
            if s["sid"] in excluded:
                continue
            poi = s["poi"]
            entry = prose.get(s["sid"], {})
            description = str(entry.get("description") or "").strip()
            tip = str(entry.get("tip") or "").strip()
            if not description:
                description, tip = _describe(poi)
            out_stops.append({
                "id": poi.get("id"), "name": poi["name"],
                "arrival_time": poi.get("arrival_time", ""),
                "duration_min": poi.get("visit_duration_min", 45),
                "description": description, "tip": tip,
                "lat": poi["lat"], "lon": poi["lon"],
                "category": poi.get("category", "attraction"),
            })
        if not out_stops:
            continue
        meta = day_meta.get(day["day"], {})
        days.append({
            "day": day["day"],
            "theme": str(meta.get("theme") or fallback_days[day["day"]]["theme"]),
            "stops": out_stops,
            "summary": str(meta.get("summary") or fallback_days[day["day"]]["summary"]),
        })

    overview = str(raw.get("overview") or "").strip() or fallback["overview"]
    return {"days": days, "overview": overview}, violations
