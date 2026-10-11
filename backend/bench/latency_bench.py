"""
End-to-end latency benchmark against the live upstreams.

Runs the full itinerary pipeline for every benchmark scenario and reports
where the time goes, stage by stage, using the same spans production emits.
Nothing is mocked: Nominatim, Overpass and the LLM are all real, so numbers
move with upstream health. Compare runs taken close together.

Run: python -m backend.bench.latency_bench --label baseline --trials 2
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from backend.bench.fixtures import SCENARIOS
from backend.core.telemetry import collect_timings

RESULTS_DIR = Path(__file__).resolve().parent / "results"
PAUSE_S = 20  # stay under the LLM provider's tokens-per-minute limit


def run_once(scenario: tuple) -> dict:
    from backend.api.routes import TripRequest, _run_pipeline

    name, city, start, goals, hours, transport = scenario
    req = TripRequest(city=city, start_location=start, duration_hours=hours,
                      goals=goals, transport=transport)
    t0 = time.perf_counter()
    with collect_timings() as timings:
        try:
            result = _run_pipeline(req)
            error = None
            stops = sum(len(d.get("stops", [])) for d in result.get("days", []))
        except Exception as e:  # a failed request is a data point, not a crash
            error = f"{type(e).__name__}: {str(e)[:120]}"
            stops = 0
    total_ms = (time.perf_counter() - t0) * 1000
    return {"scenario": name, "total_ms": round(total_ms, 1), "stages": dict(timings),
            "stops": stops, "error": error}


def summarise(runs: list[dict]) -> dict:
    ok = [r for r in runs if not r["error"]]
    out = {"runs": len(runs), "failed": len(runs) - len(ok)}
    if not ok:
        return out
    totals = [r["total_ms"] for r in ok]
    out["total"] = {"p50_ms": round(float(np.percentile(totals, 50))),
                    "p95_ms": round(float(np.percentile(totals, 95)))}
    stage_names = sorted({s for r in ok for s in r["stages"]})
    out["stages"] = {}
    for s in stage_names:
        vals = [r["stages"].get(s, 0.0) for r in ok]
        out["stages"][s] = {"p50_ms": round(float(np.percentile(vals, 50))),
                            "p95_ms": round(float(np.percentile(vals, 95))),
                            "share": round(sum(vals) / sum(totals), 3)}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--trials", type=int, default=2)
    args = parser.parse_args()

    runs = []
    for trial in range(args.trials):
        for scenario in SCENARIOS:
            run = run_once(scenario)
            run["trial"] = trial
            runs.append(run)
            status = run["error"] or f"{run['stops']} stops"
            print(f"  {run['scenario']:<18} trial {trial}  {run['total_ms'] / 1000:>6.1f}s  {status}",
                  flush=True)
            time.sleep(PAUSE_S)

    summary = summarise(runs)
    print("\n" + json.dumps(summary, indent=2))
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"latency_{args.label}.json"
    path.write_text(json.dumps({"label": args.label, "summary": summary, "runs": runs}, indent=2))
    print(f"\nWritten to {path}")


if __name__ == "__main__":
    main()
