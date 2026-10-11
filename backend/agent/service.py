"""
Everything around the agent loop: turn a stored trip into a session, run the
loop, and turn the edited stop set back into a full itinerary.

The second half reuses the pipeline's own stages rather than a lighter copy of
them, so an edited trip is routed, written and checked exactly like a new one:

    edited stops -> route (OR-Tools, within the trip's hours) -> write (LLM, or tags) -> extras

Two differences from a fresh request, both deliberate:

  * The writer may not exclude stops. On a new trip the traveller's notes can
    rule stops out; on an edit, the explicit request outranks the old notes
    (someone who wrote "no museums" and now asks for one should get it).
  * Photos already found for the kept stops are reused, and only new stops are
    looked up, with a short wait. An edit should feel quicker than a new trip.

Editing needs a model to interpret the request, so there is no model-free
fallback for the loop itself: UpstreamError from run_agent propagates and the
endpoint answers 503. Only the prose has a fallback, as in the pipeline.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from backend import config, pipeline
from backend.agent.loop import AgentRun, run_agent
from backend.agent.tools import EditSession
from backend.core.resilience import Deadline
from backend.core.telemetry import collect_timings, in_context, stage
from backend.ml.optimizer.scheduler import build_itinerary_with_meta
from backend.ml.rag.retriever import format_context, retrieve
from backend.services.wiki_photos import fetch_photos_for_itinerary

NEW_PHOTO_WAIT_S = 3.0
EMPTY_TRIP_SUMMARY = "A trip needs at least one stop, so nothing was changed."


@dataclass
class EditOutput:
    changed: bool
    changes: list[str]
    summary: str
    run: AgentRun
    result: dict = field(default_factory=dict)     # the new itinerary, when changed
    route: list[dict] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)


def reroute(session: EditSession, degraded: list[str]) -> list[dict]:
    """
    Order and time the session's stops with the same settings the pipeline used.

    The route has to fit the trip's duration, and the scheduler makes it fit
    by dropping stops from the end of the list it is given. So the list is
    built to lose the right ones: places added in this edit go first (the
    traveller just asked for them), then the original stops by ranker score.
    Anything dropped is added to the session's changes so the traveller is told.
    """
    request = session.request
    original = set(session._original_ids)
    stops = list(session.stops.values())
    added = [p for p in stops if p.get("id") not in original]
    kept = sorted((p for p in stops if p.get("id") in original),
                  key=lambda p: p.get("relevance_score", 0.0), reverse=True)
    start_lat, start_lon = session.start()
    with stage("route"):
        route, meta = build_itinerary_with_meta(
            pois=added + kept, n_days=session.n_days,
            transport=request.get("transport", "walking"), start_lat=start_lat, start_lon=start_lon,
            start_hour=pipeline.START_HOUR.get(request.get("start_time", "morning"), 9),
            deadline=session.deadline,
            day_budget_sec=pipeline.day_budgets_sec(request.get("duration_hours", 6)),
        )
    if meta["matrix"] == "haversine_fallback":
        degraded.append("matrix:haversine")
    if meta["ordering"] != "ortools":
        degraded.append("route:greedy")

    routed = {p.get("id") for day in route for p in day["pois"]}
    for sid, poi in list(session.stops.items()):
        if poi.get("id") not in routed:
            del session.stops[sid]
            session.trimmed.append(poi)
            session.changes.append(f"Dropped {poi['name']} to fit the trip's {request.get('duration_hours', 6)} hours")
    return route


def _rag_context(request: dict, degraded: list[str]) -> str:
    try:
        with stage("retrieve"):
            query = f"{request['city']} {' '.join(request.get('goals', []))}"
            return format_context(retrieve(query=query, destination=request["city"], top_k=5))
    except Exception as e:
        print(f"Retrieval unavailable: {e}")
        degraded.append("rag:unavailable")
        return ""


def _photo_map(old_itinerary: dict, route: list[dict], degraded: list[str]) -> dict[str, str]:
    """{stop name: photo url}: kept stops reuse their photo, new stops get a bounded lookup."""
    known_ids = {s.get("id") for d in old_itinerary.get("days", []) for s in d.get("stops", [])}
    photos = {s["name"]: s["photo_url"] for d in old_itinerary.get("days", [])
              for s in d.get("stops", []) if s.get("photo_url")}
    new_pois = [p for day in route for p in day["pois"] if p.get("id") not in known_ids]
    if new_pois:
        def _photos() -> dict:
            with stage("photos"):
                return fetch_photos_for_itinerary([{"pois": new_pois}])
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            photos.update(pool.submit(in_context(_photos)).result(timeout=NEW_PHOTO_WAIT_S))
        except Exception:
            degraded.append("photos:unavailable")
        pool.shutdown(wait=False)
    return photos


def run_edit(session: EditSession, message: str, degraded: list[str],
             chat=None) -> tuple[AgentRun, list[dict] | None]:
    """
    The loop, then the route for whatever it left in the session. Returns
    (run, new_route), with new_route None when the trip is to stay as it was:
    nothing changed, or the edit tried to empty the trip and is rejected whole.
    The eval calls this directly, so it measures edits without paying for prose.
    """
    with stage("agent.loop"):
        run = run_agent(session, message, chat=chat)
    if session.rejected:
        session.summary = EMPTY_TRIP_SUMMARY
        return run, None
    if not session.changed:
        return run, None
    return run, reroute(session, degraded)


def edit_trip(request: dict, route: list[dict], itinerary: dict, message: str, chat=None) -> EditOutput:
    """
    Apply a plain-language edit to a stored trip. Nothing is written to the
    database here; the caller saves the result as a new trip.
    Raises UpstreamError if no model is available, ValueError if the start
    location no longer geocodes.
    """
    deadline = Deadline(config.REQUEST_DEADLINE_S)
    session = EditSession.from_trip(request, route, itinerary, deadline=deadline)
    degraded: list[str] = []

    with collect_timings() as timings, stage("agent.edit"):
        run, new_route = run_edit(session, message, degraded, chat=chat)
        if new_route is None:
            return EditOutput(False, [], session.summary, run)

        # see the module docstring: an edit is never overridden by the trip's old notes
        trip = request | {"notes": ""}
        result, _ = pipeline.write_itinerary(trip, new_route, _rag_context(request, degraded),
                                             deadline, degraded)
        pipeline.attach_stop_extras(result, new_route, _photo_map(itinerary, new_route, degraded))

    result["degraded"] = degraded
    result["timings_ms"] = dict(timings)
    return EditOutput(True, list(session.changes), session.summary, run,
                      result=result, route=new_route, degraded=degraded)
