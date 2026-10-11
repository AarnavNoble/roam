"""
Eval for the trip editing agent: does a plain-language request end in the
right set of stops?

Each case in agent_cases.py is a request against a trip plus a programmatic
check on the stops before and after. Nothing is graded on the model's wording.
The stop set is read after re-routing, so a stop the scheduler would drop
counts as missing, exactly as it would for a user.

What is real and what is frozen:

  real    the model (one live call per turn), the loop, the tools and their
          guardrails, the ranker inside search_places, the route solver
  frozen  the places. Trips are built from the benchmark fixtures with the
          pipeline's own rank and route stages, and search_places draws from
          the same fixture (loaded into the Overpass client's cache), so a
          run does not depend on Overpass being up and two runs see the same
          candidates. A category the fixture has no places for returns
          "No places found", which some cases rely on.
  skipped the prose rewrite. It is the writer's job, has its own eval
          (contract_eval.py) and would only spend tokens here.

Trips are plain dicts in the shape of a stored Trip row (request, route,
itinerary); the database and HTTP layer are covered by the unit tests.

Run: python -m backend.bench.agent_eval [--model openai/gpt-oss-20b] [--only case_name]
"""

import argparse
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import backend.pipeline as pipeline  # imported first: lightgbm needs torch loaded before it on macOS
from backend import config
from backend.agent.service import run_edit
from backend.agent.tools import CATEGORIES, EditSession
from backend.bench.agent_cases import CASES, Case
from backend.bench.fixtures import load_all
from backend.core.resilience import UpstreamError
from backend.ml.optimizer.scheduler import build_itinerary_with_meta
from backend.ml.ranker.scorer import rank_pois
from backend.services import llm, nominatim, overpass, writer

RESULTS_PATH = Path(__file__).resolve().parent / "results" / "agent_eval.json"
DEFAULT_MODEL = "openai/gpt-oss-20b"
TRIP_DATE = "2026-10-07"      # a Wednesday, the weekday the fixtures were filtered for
TOKENS_PER_MINUTE = 6500      # stay under the provider's 8000 per model
RETRIES = 6


def build_trip(fx: dict) -> dict:
    """A trip as the pipeline would have stored it for this fixture, minus the model-written prose."""
    request = pipeline.TripRequest(
        city=fx["city"], start_location=fx["start_location"], duration_hours=fx["duration_hours"],
        goals=fx["goals"], transport=fx["transport"], trip_date=TRIP_DATE).model_dump()
    ranked = rank_pois(list(request["goals"]), [dict(p) for p in fx["pois"]],
                       top_k=pipeline.top_k(request["duration_hours"], request["pace"], len(request["goals"])))
    route, _ = build_itinerary_with_meta(
        ranked, pipeline.n_days(request["duration_hours"]), request["transport"],
        fx["start_lat"], fx["start_lon"], pipeline.START_HOUR[request["start_time"]],
        day_budget_sec=pipeline.day_budget_sec(request["duration_hours"]))
    itinerary, _ = writer.assemble(writer.fallback_raw(request, route), route, request)
    return {"request": request, "route": route, "itinerary": itinerary}


def freeze_places(fx: dict, request: dict) -> None:
    """Make geocoding and place search for this trip answer from the fixture instead of the network."""
    lat, lon = fx["start_lat"], fx["start_lon"]
    nominatim._cache.set(f"{request['start_location']}, {request['city']}".lower(), (lat, lon))
    radius = pipeline.MOBILITY_RADIUS[request["mobility"]]
    for category in CATEGORIES:
        # adventure and hidden_gems queries come back from OSM as generic attractions
        inferred = "attraction" if category in ("adventure", "hidden_gems") else category
        elements = [{"type": "node", "id": p["id"], "lat": p["lat"], "lon": p["lon"], "tags": p["tags"]}
                    for p in fx["pois"] if p["category"] == inferred]
        overpass._memory_cache.set(overpass._cache_key(lat, lon, radius, [category]), elements)


def _no_network(*args, **kwargs):
    raise UpstreamError("the eval does not call Overpass")


class PacedChat:
    """llm.chat, slowed to the provider's tokens-per-minute limit and retried when it is hit anyway."""

    def __init__(self):
        self.spent: deque[tuple[float, int]] = deque()   # (time, tokens)
        self.retries = 0

    def _wait_for_budget(self, upcoming: int) -> None:
        while True:
            now = time.monotonic()
            while self.spent and now - self.spent[0][0] > 60:
                self.spent.popleft()
            if sum(t for _, t in self.spent) + upcoming <= TOKENS_PER_MINUTE or not self.spent:
                return
            time.sleep(max(1.0, 61 - (now - self.spent[0][0])))

    def __call__(self, messages, **kwargs):
        # ~4 characters per token for the conversation, plus the tool schemas and the reply
        self._wait_for_budget(sum(len(json.dumps(m)) for m in messages) // 4 + 900)
        for attempt in range(RETRIES):
            try:
                reply, meta = llm.chat(messages, **kwargs)
            except llm.LLMUnavailable:
                if attempt == RETRIES - 1:
                    raise
                self.retries += 1
                llm._breakers.clear()     # with one model allowed, an open breaker means no eval
                time.sleep(15 * (attempt + 1))
                continue
            self.spent.append((time.monotonic(), meta["prompt_tokens"] + meta["completion_tokens"]))
            return reply, meta


def _stops(route: list[dict]) -> list[dict]:
    return [p for day in route for p in day["pois"]]


def run_case(case: Case, trip: dict, chat: PacedChat) -> dict:
    before = _stops(trip["route"])
    out = {"name": case.name, "kind": case.kind, "trip": case.trip, "passed": False, "reason": "",
           "message": "", "steps": 0, "prompt_tokens": 0, "completion_tokens": 0}
    try:
        target = case.target(before) if case.target else None
    except LookupError as e:
        return out | {"reason": f"setup: {e}"}
    out["message"] = message = case.message.format(target=target["name"] if target else "")

    session = EditSession.from_trip(trip["request"], trip["route"], trip["itinerary"])
    started = time.perf_counter()
    try:
        run, new_route = run_edit(session, message, [], chat=chat)
    except UpstreamError as e:
        # The endpoint answers 503 here and saves nothing, so the trip is as it
        # was. The case is scored on that outcome like any other, and the error
        # is recorded so a pass of this kind can be told from a clean one.
        reason = case.check(before, before, target, set())
        return out | {"passed": reason is None, "reason": reason or "", "outcome": "error_503",
                      "error": str(e)[:300], "before": [p["name"] for p in before],
                      "after": [p["name"] for p in before]}
    after = before if new_route is None else _stops(new_route)

    reason = case.check(before, after, target, {p["id"] for p in session.trimmed})
    return out | {
        "passed": reason is None, "reason": reason or "", "outcome": "edited" if new_route else "unchanged",
        "steps": run.steps, "prompt_tokens": run.prompt_tokens, "completion_tokens": run.completion_tokens,
        "stop_reason": run.stop_reason, "seconds": round(time.perf_counter() - started, 1),
        "tool_calls": [f"{t['tool']}({t['args']}) -> {t['result'][:80]}" for t in run.trace],
        "summary": session.summary,
        "before": [p["name"] for p in before], "after": [p["name"] for p in after],
        "trimmed_for_time": [p["name"] for p in session.trimmed],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--only", default="", help="run a single case by name")
    args = parser.parse_args()

    config.GROQ_MODELS = [args.model]
    config.ORS_API_KEY = ""                        # straight-line travel times: same route every run
    overpass._query_overpass = _no_network

    fixtures = {fx["name"]: fx for fx in load_all() if fx.get("pois")}
    cases = [c for c in CASES if not args.only or c.name == args.only]
    trips = {}
    for name in sorted({c.trip for c in cases}):
        if name not in fixtures:
            raise SystemExit(f"fixture {name} is missing or empty; run python -m backend.bench.fixtures --refresh")
        trips[name] = build_trip(fixtures[name])
        freeze_places(fixtures[name], trips[name]["request"])
        print(f"{name}: {len(_stops(trips[name]['route']))} stops from {len(fixtures[name]['pois'])} candidates")

    chat = PacedChat()
    results = []
    for case in cases:
        result = run_case(case, trips[case.trip], chat)
        results.append(result)
        tokens = result["prompt_tokens"] + result["completion_tokens"]
        print(f"  {'PASS' if result['passed'] else 'FAIL'}  {case.name:<28} {result['steps']} turns "
              f"{tokens:>5} tok  {result.get('outcome', '')}  {result['reason']}", flush=True)

    ran = [r for r in results if r["steps"]]
    passed = sum(r["passed"] for r in results)
    summary = {
        "model": args.model, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": len(results), "passed": passed, "pass_rate": round(passed / len(results), 3),
        "mean_turns": round(sum(r["steps"] for r in ran) / max(len(ran), 1), 2),
        "mean_tokens": round(sum(r["prompt_tokens"] + r["completion_tokens"] for r in ran) / max(len(ran), 1)),
        "rate_limit_retries": chat.retries,
        "ended_in_503": [r["name"] for r in results if r.get("outcome") == "error_503"],
        "edits_that_trimmed_a_stop_for_time": sum(bool(r.get("trimmed_for_time")) for r in results),
        "places": "frozen: trips and search candidates come from backend/bench/fixtures, no live Overpass",
        "by_kind": {k: f"{sum(r['passed'] for r in results if r['kind'] == k)}/{sum(r['kind'] == k for r in results)}"
                    for k in sorted({r["kind"] for r in results})},
    }
    print(f"\n{passed}/{len(results)} passed ({summary['pass_rate']:.0%}), "
          f"{summary['mean_turns']} turns and {summary['mean_tokens']} tokens per edit on {args.model}")
    if not args.only:
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps(summary | {"results": results}, indent=2, ensure_ascii=False))
        print(f"wrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
