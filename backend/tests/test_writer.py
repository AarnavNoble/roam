"""
The LLM contract: the model writes prose keyed by stop number and nothing
else. These tests pin what counts as a violation and that assemble() returns a
complete itinerary whatever the model sent.
"""

import pytest

from backend.services import writer
from backend.services.writer import assemble, build_prompt, fallback_raw, flatten, validate


def poi(id_: int, name: str, category: str = "culture", **tags) -> dict:
    return {"id": id_, "name": name, "lat": 48.0 + id_ / 1000, "lon": 2.0 + id_ / 1000,
            "category": category, "arrival_time": f"{9 + id_ % 10:02d}:15", "visit_duration_min": 90,
            "tags": tags}


def make_route() -> list[dict]:
    """Two days: stops 1-3 on day 1, stop 4 on day 2."""
    return [
        {"day": 1, "travel_times_sec": [300, 720], "total_travel_min": 17, "pois": [
            poi(11, "Musee A", tourism="museum"),
            poi(12, "Cafe B", "food", cuisine="coffee_shop", opening_hours="Mo-Su 08:00-18:00"),
            poi(13, "Parc C", "nature", leisure="park", description="A terraced park with a view."),
        ]},
        {"day": 2, "travel_times_sec": [], "total_travel_min": 0, "pois": [poi(14, "Galerie D", tourism="gallery")]},
    ]


TRIP = {"city": "Paris", "start_location": "Montmartre", "duration_hours": 12, "goals": ["culture", "food"],
        "notes": ""}
TRIP_WITH_NOTES = TRIP | {"notes": "no museums"}


def good_raw() -> dict:
    return {
        "overview": "Two easy days.",
        "days": [{"day": 1, "theme": "Hill", "summary": "Up the hill."},
                 {"day": 2, "theme": "Art", "summary": "One gallery."}],
        "stops": [{"id": i, "description": f"Prose {i}.", "tip": f"Tip {i}."} for i in (1, 2, 3, 4)],
        "excluded": [],
    }


def codes(violations) -> list[str]:
    return [v.code for v in violations]


# ── flatten / build_prompt ───────────────────────────────────────────────────

def test_flatten_numbers_stops_across_days():
    stops = flatten(make_route())
    assert [(s["sid"], s["day"], s["poi"]["name"]) for s in stops] == [
        (1, 1, "Musee A"), (2, 1, "Cafe B"), (3, 1, "Parc C"), (4, 2, "Galerie D")]
    # travel to the next stop in minutes; the last stop of a day has none
    assert [s["travel_to_next_min"] for s in stops] == [5, 12, None, None]


def test_prompt_lists_every_stop_by_number_with_time_and_tags():
    prompt = build_prompt(TRIP, make_route(), "")
    assert "[1] 10:15 Musee A (culture, 90 min) tourism=museum -> 5 min to next" in prompt
    assert "[4] 13:15 Galerie D" in prompt
    assert "Day 1:" in prompt and "Day 2:" in prompt
    assert "opening_hours=Mo-Su 08:00-18:00" in prompt


def test_prompt_never_shows_or_asks_for_facts_the_server_owns():
    # Coordinates are not in the prompt, and the output shape has no name, time or coordinate field,
    # so the model has nothing to copy wrongly.
    prompt = build_prompt(TRIP, make_route(), "")
    for p in (p for day in make_route() for p in day["pois"]):
        assert str(p["lat"]) not in prompt and str(p["lon"]) not in prompt
    shape = prompt[prompt.index("Return JSON exactly in this shape"):]
    for field in ('"lat"', '"lon"', '"name"', '"arrival_time"', '"duration_min"', '"category"'):
        assert field not in shape
    assert '"stops": [{"id": 1, "description": "...", "tip": "..."}]' in shape


def test_prompt_forbids_exclusions_unless_the_traveller_left_notes():
    without = build_prompt(TRIP, make_route(), "")
    assert '"excluded" must be an empty list' in without
    assert "TRAVELER'S NOTES" not in without
    with_notes = build_prompt(TRIP_WITH_NOTES, make_route(), "")
    assert 'TRAVELER\'S NOTES: "no museums"' in with_notes
    assert '"excluded" must be an empty list' not in with_notes


def test_prompt_includes_profile_dietary_and_truncated_context():
    trip = TRIP | {"dietary": "vegan", "transport": "transit", "pace": "packed", "style": "family"}
    prompt = build_prompt(trip, make_route(), "x" * 5000)
    assert "- Dietary: vegan" in prompt
    assert "getting around by public transit" in prompt
    assert "maximize stops" in prompt and "family with kids" in prompt
    assert "x" * writer.MAX_CONTEXT_CHARS in prompt
    assert "x" * (writer.MAX_CONTEXT_CHARS + 1) not in prompt
    assert "Dietary" not in build_prompt(TRIP, make_route(), "")


# ── validate: one test per violation code ────────────────────────────────────

def test_conforming_answer_has_no_violations():
    assert validate(good_raw(), make_route(), allow_exclusions=False) == []


@pytest.mark.parametrize("stops", [None, "1, 2, 3", {"1": "x"}])
def test_bad_shape_when_stops_is_not_a_list(stops):
    raw = good_raw() | {"stops": stops}
    assert codes(validate(raw, make_route(), False)) == ["bad_shape"]


@pytest.mark.parametrize("entry", ["stop one", {"description": "no id"}, {"id": "1", "description": "x"},
                                   {"id": True, "description": "x"}, {"id": 1.0, "description": "x"}])
def test_bad_shape_when_an_entry_has_no_integer_id(entry):
    raw = good_raw()
    raw["stops"][0] = entry
    # the entry is rejected, so stop 1 is also reported as not described
    assert codes(validate(raw, make_route(), False)) == ["bad_shape", "missing_stop"]


def test_unknown_stop():
    raw = good_raw()
    raw["stops"].append({"id": 9, "description": "Invented."})
    violations = validate(raw, make_route(), False)
    assert codes(violations) == ["unknown_stop"]
    assert "9" in violations[0].detail


def test_duplicate_stop():
    raw = good_raw()
    raw["stops"].append({"id": 2, "description": "Again."})
    assert codes(validate(raw, make_route(), False)) == ["duplicate_stop"]


@pytest.mark.parametrize("description", ["", "   ", None])
def test_empty_description(description):
    raw = good_raw()
    raw["stops"][2]["description"] = description
    assert codes(validate(raw, make_route(), False)) == ["empty_description"]


def test_missing_stop_lists_each_one():
    raw = good_raw()
    raw["stops"] = raw["stops"][:2]
    violations = validate(raw, make_route(), False)
    assert codes(violations) == ["missing_stop", "missing_stop"]
    assert [v.detail for v in violations] == ["id 3 was not described", "id 4 was not described"]


@pytest.mark.parametrize("overview", ["", "  ", None])
def test_missing_overview(overview):
    raw = good_raw() | {"overview": overview}
    assert codes(validate(raw, make_route(), False)) == ["missing_overview"]


def test_illegal_exclusion_without_notes():
    # Excluding is only legal when the traveller wrote notes. The excluded stop then also counts
    # as missing, because nothing excuses it from being described.
    raw = good_raw() | {"excluded": [4]}
    raw["stops"] = raw["stops"][:3]
    assert codes(validate(raw, make_route(), allow_exclusions=False)) == ["illegal_exclusion", "missing_stop"]


def test_illegal_exclusion_when_more_than_half_the_route_goes():
    raw = good_raw() | {"excluded": [1, 2, 3]}
    assert codes(validate(raw, make_route(), allow_exclusions=True)) == ["illegal_exclusion"]


def test_excluded_must_be_a_list():
    raw = good_raw() | {"excluded": "2"}
    assert codes(validate(raw, make_route(), allow_exclusions=True)) == ["bad_shape"]


def test_legal_exclusion_need_not_be_described():
    # With notes, up to half the route may be excluded, and excluded stops need no prose.
    raw = good_raw() | {"excluded": [1, 4]}
    raw["stops"] = raw["stops"][1:3]
    assert validate(raw, make_route(), allow_exclusions=True) == []


def test_excluding_an_id_that_is_not_in_the_route_excuses_nothing():
    raw = good_raw() | {"excluded": [9]}
    raw["stops"] = raw["stops"][:3]
    assert codes(validate(raw, make_route(), allow_exclusions=True)) == ["missing_stop"]


def test_several_violations_are_all_reported():
    raw = {"stops": [{"id": 1, "description": ""}, {"id": 1, "description": "dup"}, {"id": 7, "description": "x"}]}
    assert codes(validate(raw, make_route(), False)) == [
        "empty_description", "duplicate_stop", "unknown_stop",
        "missing_stop", "missing_stop", "missing_stop", "missing_overview"]


# ── assemble ─────────────────────────────────────────────────────────────────

def all_stops(itinerary: dict) -> list[dict]:
    return [s for d in itinerary["days"] for s in d["stops"]]


def assert_route_facts(itinerary: dict, route: list[dict], skipped: set[int] = frozenset()) -> None:
    """Stops equal the route in id, name, coordinates, time and duration, in order."""
    expected = [p for day in route for p in day["pois"] if p["id"] not in skipped]
    got = all_stops(itinerary)
    assert [(s["id"], s["name"], s["lat"], s["lon"], s["arrival_time"], s["duration_min"], s["category"])
            for s in got] == [(p["id"], p["name"], p["lat"], p["lon"], p["arrival_time"],
                               p["visit_duration_min"], p["category"]) for p in expected]
    assert all(s["description"].strip() for s in got)


def test_assemble_joins_prose_onto_route_facts():
    itinerary, violations = assemble(good_raw(), make_route(), TRIP)
    assert violations == []
    assert_route_facts(itinerary, make_route())
    assert [s["description"] for s in all_stops(itinerary)] == ["Prose 1.", "Prose 2.", "Prose 3.", "Prose 4."]
    assert [s["tip"] for s in all_stops(itinerary)] == ["Tip 1.", "Tip 2.", "Tip 3.", "Tip 4."]
    assert itinerary["overview"] == "Two easy days."
    assert [(d["day"], d["theme"], d["summary"]) for d in itinerary["days"]] == [
        (1, "Hill", "Up the hill."), (2, "Art", "One gallery.")]


def test_assemble_ignores_facts_the_model_tries_to_supply():
    # The model renames stop 1 and moves it across the city. None of it survives.
    raw = good_raw()
    raw["stops"][0] |= {"name": "Louvre", "lat": 1.0, "lon": 1.0, "arrival_time": "03:00", "duration_min": 5}
    itinerary, _ = assemble(raw, make_route(), TRIP)
    assert_route_facts(itinerary, make_route())
    assert "Louvre" not in str(itinerary)


def test_assemble_fills_missing_and_blank_stops_from_tags():
    raw = good_raw()
    raw["stops"] = [raw["stops"][0], {"id": 2, "description": " "}]
    itinerary, violations = assemble(raw, make_route(), TRIP)
    assert set(codes(violations)) == {"empty_description", "missing_stop"}
    assert_route_facts(itinerary, make_route())
    stops = all_stops(itinerary)
    assert stops[0]["description"] == "Prose 1."
    assert stops[1]["description"].startswith("Cafe B is a coffee shop stop")
    assert stops[1]["tip"] == "Opening hours: Mo-Su 08:00-18:00."
    assert stops[2]["description"] == "A terraced park with a view."  # the OSM description tag
    assert stops[3]["tip"] == "Check opening hours before you go."


def test_assemble_keeps_first_of_duplicates_and_drops_unknown_ids():
    raw = good_raw()
    raw["stops"] += [{"id": 1, "description": "Second take."}, {"id": 42, "description": "Invented."}]
    itinerary, violations = assemble(raw, make_route(), TRIP)
    assert codes(violations) == ["duplicate_stop", "unknown_stop"]
    assert_route_facts(itinerary, make_route())
    assert "Second take." not in str(itinerary) and "Invented." not in str(itinerary)


def test_assemble_applies_a_legal_exclusion_and_drops_an_emptied_day():
    raw = good_raw() | {"excluded": [4]}
    itinerary, violations = assemble(raw, make_route(), TRIP_WITH_NOTES)
    assert violations == []
    assert_route_facts(itinerary, make_route(), skipped={14})
    assert [d["day"] for d in itinerary["days"]] == [1]  # day 2 had only the excluded stop


@pytest.mark.parametrize("trip,excluded", [(TRIP, [2]), (TRIP_WITH_NOTES, [1, 2, 3]), (TRIP | {"notes": "  "}, [2])])
def test_assemble_ignores_illegal_exclusions(trip, excluded):
    # No notes (or whitespace only), or more than half the route: every stop stays.
    itinerary, violations = assemble(good_raw() | {"excluded": excluded}, make_route(), trip)
    assert "illegal_exclusion" in codes(violations)
    assert_route_facts(itinerary, make_route())


def test_assemble_falls_back_for_missing_overview_and_day_text():
    raw = {"stops": good_raw()["stops"]}
    itinerary, violations = assemble(raw, make_route(), TRIP)
    assert codes(violations) == ["missing_overview"]
    assert itinerary["overview"].startswith("A 12-hour route through Paris built around culture, food")
    assert itinerary["days"][0]["theme"] == "Culture And Food"
    assert itinerary["days"][0]["summary"] == "3 stops, about 17 minutes of travel."


@pytest.mark.parametrize("raw", [
    {},
    {"stops": 5, "days": 7, "excluded": 3, "overview": 9},
    {"stops": [None, 3, "x", [], {"id": "1"}, {"id": None}], "days": "monday"},
    {"stops": [{"id": 1}], "days": [None, 4, {"day": [1]}, {"day": 1, "theme": None}], "excluded": {"a": 1}},
    {"stops": [{"id": i, "description": ["not", "a", "string"], "tip": {"k": 1}} for i in (1, 2, 3, 4)]},
    {"stops": [{"id": 2 ** 70, "description": "x"}], "excluded": [True, None, "2", [1]]},
])
def test_assemble_survives_any_json_object(raw):
    # Whatever shape the model sends, the result is the full route with text on every stop.
    for trip in (TRIP, TRIP_WITH_NOTES):
        itinerary, _ = assemble(raw, make_route(), trip)
        assert_route_facts(itinerary, make_route())
        assert itinerary["overview"].strip()
        assert all(isinstance(d["theme"], str) and d["theme"] and d["summary"] for d in itinerary["days"])


# ── fallback_raw ─────────────────────────────────────────────────────────────

def test_fallback_answer_satisfies_the_contract_it_replaces():
    raw = fallback_raw(TRIP, make_route())
    assert validate(raw, make_route(), allow_exclusions=False) == []
    assert raw["excluded"] == []
    assert [s["id"] for s in raw["stops"]] == [1, 2, 3, 4]
    assert [d["day"] for d in raw["days"]] == [1, 2]


def test_fallback_text_is_built_from_tags():
    raw = fallback_raw(TRIP, make_route())
    assert raw["stops"][0]["description"] == (
        "Musee A is a museum stop picked for your culture interest. Plan about 90 minutes here.")
    assert raw["stops"][1]["tip"] == "Opening hours: Mo-Su 08:00-18:00."
    assert "starting from Montmartre" in raw["overview"]
