"""
Itinerary contract eval: does the model's answer match the route it was given?

The route (which stops, what order, what time, which coordinates) is fixed
before the model is called. Two ways of asking the model to write it up are
compared on the same routes:

  legacy   the original prompt: the model echoes every stop back as a full
           JSON object (name, time, coordinates, category) plus prose
  keyed    the current prompt (services/writer.py): the model returns prose
           keyed by stop number and the server joins it onto the route

--record   builds real routes from live data, calls the model both ways, and
           stores the raw answers with their token usage. Needs GROQ_API_KEY.
--check    offline. Replays the stored answers through the validators and the
           assembler. Reports how often each format broke the contract, and
           exits non-zero if any assembled itinerary differs from its route in
           a single stop, coordinate or time. This is what CI runs: the
           recordings are real model outputs, so the gate catches a change to
           the validator or assembler that would let a bad answer through.

Run: python -m backend.bench.contract_eval --check
"""

import argparse
import json
import sys
import time
from pathlib import Path

RECORDINGS = Path(__file__).resolve().parent / "contract" / "recordings.json"
RESULTS = Path(__file__).resolve().parent / "results" / "contract.json"
PAUSE_S = 45  # tokens-per-minute limit on the provider

# (scenario index into fixtures.SCENARIOS, notes)
NOTES = [
    "",
    "I love street food and want great photo spots.",
    "No bars or pubs please, I don't drink.",
    "I've already seen the main museum, skip museums.",
]


def build_cases() -> list[dict]:
    """Two trips per benchmark scenario: one without notes, one with."""
    from backend.bench.fixtures import SCENARIOS
    cases = []
    for i, (name, city, start, goals, hours, transport) in enumerate(SCENARIOS):
        for variant, notes in ((0, NOTES[0]), (1, NOTES[1 + i % 3])):
            cases.append({"case": f"{name}-{variant}", "city": city, "start_location": start,
                          "goals": goals, "duration_hours": hours, "transport": transport,
                          "notes": notes})
    return cases


def build_route(case: dict) -> tuple[dict, list[dict], str]:
    """Run the pipeline up to (not including) the model. Returns (trip, route, rag_context)."""
    from backend.pipeline import TripRequest, effective_goals, n_days, top_k
    from backend.ml.optimizer.scheduler import build_itinerary_with_meta
    from backend.ml.rag.retriever import format_context, retrieve
    from backend.ml.ranker.scorer import rank_pois
    from backend.services.nominatim import geocode_with_source
    from backend.services.overpass import fetch_pois_with_source

    req = TripRequest(**{k: v for k, v in case.items() if k != "case"})
    lat, lon, _ = geocode_with_source(f"{req.start_location}, {req.city}")
    pois, _ = fetch_pois_with_source(lat, lon, effective_goals(req.goals, req.familiarity),
                                     radius_m=2000, visit_start_h=9,
                                     visit_end_h=min(9 + req.duration_hours, 23), weekday=2)
    ranked = rank_pois(list(req.goals), [p.to_dict() for p in pois],
                       top_k=top_k(req.duration_hours, req.pace, len(req.goals)))
    route, _ = build_itinerary_with_meta(ranked, n_days(req.duration_hours), req.transport, lat, lon, 9)
    context = format_context(retrieve(f"{req.city} {' '.join(req.goals)}", req.city, top_k=5))
    return req.model_dump() | {"start_lat": lat, "start_lon": lon}, route, context


def record() -> None:
    from backend.bench.legacy_prompt import build_prompt as legacy_prompt
    from backend.services import llm, writer

    from backend.db.session import run_migrations
    run_migrations()  # the POI cache table must exist

    done = json.loads(RECORDINGS.read_text()) if RECORDINGS.exists() else []
    have = {r["case"] for r in done}
    for case in build_cases():
        if case["case"] in have:
            continue
        try:
            trip, route, context = build_route(case)
        except Exception as e:
            print(f"  {case['case']}: route failed ({type(e).__name__}: {e})")
            continue
        entry = {"case": case["case"], "trip": trip, "route": route, "answers": {}}
        prompts = {"keyed": writer.build_prompt(trip, route, context),
                   "legacy": legacy_prompt(trip, route, context)}
        for fmt, prompt in prompts.items():
            t0 = time.perf_counter()
            try:
                raw, meta = llm.complete_json(prompt, max_tokens=4000)
                entry["answers"][fmt] = {
                    "raw": raw, "model": meta["model"], "prompt_tokens": meta["prompt_tokens"],
                    "completion_tokens": meta["completion_tokens"],
                    "seconds": round(time.perf_counter() - t0, 2)}
            except Exception as e:
                entry["answers"][fmt] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            time.sleep(PAUSE_S)
        done.append(entry)
        RECORDINGS.parent.mkdir(exist_ok=True)
        RECORDINGS.write_text(json.dumps(done, indent=1))
        print(f"  {case['case']}: " + ", ".join(
            f"{f}={'error' if 'error' in a else a['completion_tokens']}" for f, a in entry["answers"].items()),
            flush=True)


def _route_stops(route: list[dict]) -> list[dict]:
    return [p for day in route for p in day["pois"]]


def legacy_violations(raw: dict, route: list[dict], has_notes: bool) -> list[str]:
    """Contract check for the echo format: the answer must restate the route exactly."""
    expected = _route_stops(route)
    got = [s for d in raw.get("days", []) if isinstance(d, dict) for s in d.get("stops", [])
           if isinstance(s, dict)]
    out = []
    names = [p["name"] for p in expected]
    got_names = [s.get("name") for s in got]
    invented = [n for n in got_names if n not in names]
    dropped = [n for n in names if n not in got_names]
    if invented:
        out.append("invented_stop")
    if dropped and not has_notes:  # with notes the old prompt allowed removals
        out.append("dropped_stop")
    if len(got_names) != len(set(got_names)):
        out.append("duplicate_stop")
    kept = [n for n in got_names if n in names]
    if kept != [n for n in names if n in kept]:
        out.append("reordered")
    by_name = {p["name"]: p for p in expected}
    for s in got:
        p = by_name.get(s.get("name"))
        if p is None:
            continue
        try:
            if abs(float(s.get("lat", 0)) - p["lat"]) > 1e-4 or abs(float(s.get("lon", 0)) - p["lon"]) > 1e-4:
                out.append("wrong_coordinates")
        except (TypeError, ValueError):
            out.append("wrong_coordinates")
        if s.get("arrival_time") != p.get("arrival_time"):
            out.append("wrong_time")
        if not str(s.get("description") or "").strip():
            out.append("empty_description")
    return sorted(set(out))


def assembled_mismatches(itinerary: dict, route: list[dict], excluded: set[int]) -> list[str]:
    """Differences between an assembled itinerary and its route. Must be empty."""
    from backend.services import writer
    expected = [s["poi"] for s in writer.flatten(route) if s["sid"] not in excluded]
    got = [s for d in itinerary["days"] for s in d["stops"]]
    if len(got) != len(expected):
        return [f"{len(got)} stops, expected {len(expected)}"]
    out = []
    for s, p in zip(got, expected):
        if (s["name"], s["lat"], s["lon"], s["arrival_time"]) != (
                p["name"], p["lat"], p["lon"], p.get("arrival_time", "")):
            out.append(f"{s['name']}: facts differ from route")
        if not s["description"].strip():
            out.append(f"{s['name']}: empty description")
    return out


def check() -> int:
    from backend.services import writer

    if not RECORDINGS.exists():
        print("No recordings. Run with --record first.")
        return 1
    recordings = json.loads(RECORDINGS.read_text())
    stats = {f: {"answers": 0, "violating": 0, "codes": {}, "prompt_tokens": [], "completion_tokens": [],
                 "seconds": []} for f in ("legacy", "keyed")}
    mismatches = []

    for rec in recordings:
        route, trip = rec["route"], rec["trip"]
        has_notes = bool(trip.get("notes", "").strip())
        for fmt, answer in rec["answers"].items():
            if "error" in answer:
                continue
            s = stats[fmt]
            s["answers"] += 1
            for key in ("prompt_tokens", "completion_tokens", "seconds"):
                s[key].append(answer[key])
            if fmt == "legacy":
                codes = legacy_violations(answer["raw"], route, has_notes)
            else:
                codes = sorted({v.code for v in writer.validate(answer["raw"], route, has_notes)})
                itinerary, _ = writer.assemble(answer["raw"], route, trip)
                valid = {st["sid"] for st in writer.flatten(route)}
                excluded = ({e for e in answer["raw"].get("excluded") or [] if e in valid}
                            if has_notes and "illegal_exclusion" not in codes else set())
                mismatches += [f"{rec['case']}: {m}" for m in assembled_mismatches(itinerary, route, excluded)]
            if codes:
                s["violating"] += 1
                for c in codes:
                    s["codes"][c] = s["codes"].get(c, 0) + 1

    summary = {"cases": len(recordings)}
    for fmt, s in stats.items():
        n = s["answers"]
        if not n:
            continue
        mean = lambda xs: round(sum(xs) / len(xs), 1)
        summary[fmt] = {"answers": n, "violating": s["violating"],
                        "violation_rate": round(s["violating"] / n, 3), "violations": s["codes"],
                        "mean_prompt_tokens": mean(s["prompt_tokens"]),
                        "mean_completion_tokens": mean(s["completion_tokens"]),
                        "mean_seconds": mean(s["seconds"])}
    summary["assembled_mismatches"] = mismatches
    print(json.dumps(summary, indent=2))
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(summary, indent=2))

    if mismatches:
        print(f"\nFAIL: {len(mismatches)} assembled itineraries differ from their route")
        return 1
    print("\nOK: every assembled itinerary matches its route")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.record:
        record()
    sys.exit(check() if args.check or not args.record else 0)
