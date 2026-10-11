"""
Builds the ranker's labelled relevance set: for a (goals, POI) pair, how good a
stop is this place for a traveller with these goals, on a 0 to 4 scale.

These are SILVER labels. They come from an LLM judge, not from people and not
from user behaviour. That is a real limitation and it should be said out loud
whenever a number computed from them is quoted: the benchmark measures how well
the ranker agrees with the judge on places it has not seen, not how much users
like the itineraries.

What keeps the labels honest:
  - The judge sees only what a person skimming a map would see: the place name,
    its category and a few descriptive OSM tags. It never sees a model feature
    (no similarity score, no notability flag, no tag count), so the model cannot
    score well by reading back its own inputs. Two caveats: the category shown
    to the judge is the same category the model's match features use, and the
    judge can recognise a famous name, which is exactly what the notability
    feature tries to approximate from the wikipedia tag.
  - Every label file records the judge model, the full prompt template and the
    batch settings next to the labels, so the set can be regenerated and audited.
  - POIs are shuffled (with a fixed seed) before batching, so a batch is a mix
    of categories and the judge grades each place on the same scale.
  - `--audit` judges a random sample a second time, in a different order, and
    reports how often the judge agrees with itself.

Each city's candidate pool (bench/fixtures/<city>.json) is judged against five
different goal sets. A city whose fixture is empty (Overpass was down when it
was fetched) is skipped, so the set covers whichever cities have a pool; after
refetching a fixture, rerunning this script labels just the new city.

Run:   python -m backend.bench.label_pois            (resumes; skips finished queries)
       python -m backend.bench.label_pois --sample   (print labels for a spot check)
       python -m backend.bench.label_pois --audit    (test-retest agreement of the judge)
       python -m backend.bench.label_pois --second-opinion MODEL   (agreement with another judge)
"""

import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
LABEL_DIR = Path(__file__).resolve().parent / "labels"

JUDGE_MODEL = "qwen/qwen3.8-27b"
BATCH_SIZE = 40
SHUFFLE_SEED = 0
TOKENS_PER_MINUTE = 8000   # provider limit for this model
TAGS_SHOWN = ["cuisine", "amenity", "tourism", "historic", "leisure", "natural", "shop"]

# Five goal sets per city. The first is the scenario's own goals; the others
# are chosen so that every one of the app's eight goals is asked for in
# several cities, alone and in combination.
QUERIES = {
    "paris_marais":      [["food", "history"], ["culture", "shopping"], ["nature"], ["nightlife"],
                          ["hidden_gems", "culture", "food"]],
    "tokyo_shinjuku":    [["food", "nightlife"], ["history", "nature"], ["shopping"], ["culture"],
                          ["adventure", "food", "shopping"]],
    "rome_trastevere":   [["history", "culture"], ["food"], ["nightlife", "hidden_gems"], ["shopping", "nature"],
                          ["adventure", "history"]],
    "london_soho":       [["nightlife", "food"], ["culture"], ["shopping", "nature", "history"], ["adventure"],
                          ["hidden_gems", "food"]],
    "barcelona_gothic":  [["culture", "food"], ["history", "hidden_gems"], ["nightlife"], ["shopping"],
                          ["nature", "adventure"]],
    "nyc_village":       [["food", "shopping"], ["nature", "culture"], ["adventure", "nightlife"], ["history"],
                          ["hidden_gems", "food"]],
    "sydney_bondi":      [["nature", "food"], ["adventure"], ["culture", "history", "shopping"], ["nightlife"],
                          ["hidden_gems", "nature"]],
    "bangkok_sukhumvit": [["food", "shopping"], ["nightlife", "culture"], ["nature", "adventure", "hidden_gems"],
                          ["history"], ["food"]],
}

PROMPT = """You are grading candidate stops for a travel itinerary.

A traveller starting from {start_location}, {city} told a trip planner their goals for the day:
{goals}

Rate how good a stop each place below would be for THIS traveller, from 0 to 4:
4 = excellent: clearly matches a goal and is a standout or well-known place of its kind
3 = good: clearly matches at least one goal
2 = possible: a partial or generic match
1 = weak: only loosely related to the goals
0 = not relevant to these goals, or not somewhere a visitor would go

Judge only from the name, category and tags given. Use the whole scale.

Places (number | name | category | tags):
{places}

Answer with a JSON object of the form {{"ratings": {{"1": 3, "2": 0}}}} with one entry per place number and nothing else."""


def _place_line(n: int, poi: dict) -> str:
    tags = ", ".join(f"{k}={poi['tags'][k]}" for k in TAGS_SHOWN if poi.get("tags", {}).get(k))
    return f"{n} | {poi['name']} | {poi['category']} | {tags or '-'}"


def build_prompt(fixture: dict, goals: list[str], batch: list[dict]) -> str:
    return PROMPT.format(
        start_location=fixture["start_location"], city=fixture["city"],
        goals=", ".join(g.replace("_", " ") for g in goals),
        places="\n".join(_place_line(i + 1, p) for i, p in enumerate(batch)),
    )


_spent: list[tuple[float, int]] = []   # (time, tokens) of recent calls


def _wait_for_budget(needed: int) -> None:
    """Sleep until `needed` more tokens fit in the last minute's allowance."""
    while True:
        now = time.time()
        _spent[:] = [(t, n) for t, n in _spent if now - t < 60]
        if sum(n for _, n in _spent) + needed <= TOKENS_PER_MINUTE * 0.9:
            return
        time.sleep(5)


def judge_batch(fixture: dict, goals: list[str], batch: list[dict], model: str = JUDGE_MODEL) -> list[int]:
    """One LLM call. Returns a 0 to 4 label per POI in batch order, or raises after retries."""
    from backend import config
    config.GROQ_MODELS = [model]   # no failover: one judge for the whole set
    from backend.services import llm

    prompt = build_prompt(fixture, goals, batch)
    estimate = len(prompt) // 3 + 8 * len(batch)
    last_error = ""
    for attempt in range(8):
        _wait_for_budget(estimate)
        try:
            # A reasoning model (the second judge) also spends output tokens on thinking
            message, meta = llm.chat([{"role": "user", "content": prompt}], json_mode=True,
                                     max_tokens=12 * len(batch) + (50 if model == JUDGE_MODEL else 1500),
                                     temperature=0.0)
        except llm.LLMUnavailable as e:
            # Usually a 429. The client opens a circuit breaker on failures; for
            # a batch job we would rather wait and call the same model again.
            last_error = str(e)[:200]
            llm._breakers.clear()
            time.sleep(20 * (attempt + 1))
            continue
        _spent.append((time.time(), meta["prompt_tokens"] + meta["completion_tokens"]))
        try:
            ratings = json.loads(message.content or "")["ratings"]
            labels = [int(ratings[str(i + 1)]) for i in range(len(batch))]
        except (KeyError, TypeError, ValueError):
            last_error = "answer did not have one integer per place"
            continue
        if all(0 <= x <= 4 for x in labels):
            return labels
        last_error = "label outside 0 to 4"
    raise RuntimeError(f"judge failed for {fixture['name']} {goals}: {last_error}")


def judge_query(fixture: dict, goals: list[str], seed: int = SHUFFLE_SEED,
                model: str = JUDGE_MODEL) -> dict[str, int]:
    """Label every POI in the fixture for one goal set. Returns {poi_id: label}."""
    pois = list(fixture["pois"])
    random.Random(seed).shuffle(pois)
    labels: dict[str, int] = {}
    for i in range(0, len(pois), BATCH_SIZE):
        batch = pois[i:i + BATCH_SIZE]
        for poi, label in zip(batch, judge_batch(fixture, goals, batch, model)):
            labels[str(poi["id"])] = label
        print(f"    {len(labels)}/{len(pois)}", flush=True)
    return labels


def _judge_record() -> dict:
    return {"model": JUDGE_MODEL, "temperature": 0.0, "batch_size": BATCH_SIZE,
            "shuffle_seed": SHUFFLE_SEED, "tags_shown": TAGS_SHOWN, "prompt_template": PROMPT}


def label_all() -> None:
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, goal_sets in QUERIES.items():
        fixture = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
        if not fixture["pois"]:
            print(f"{name}: fixture has no POIs, skipping")
            continue
        path = LABEL_DIR / f"{name}.json"
        out = json.loads(path.read_text()) if path.exists() else {
            "fixture": name, "city": fixture["city"], "label_source": "LLM judge (silver labels)",
            "judge": _judge_record(), "queries": []}
        poi_ids = {str(p["id"]) for p in fixture["pois"]}
        for goals in goal_sets:
            done = [q for q in out["queries"] if q["goals"] == goals and set(q["labels"]) == poi_ids]
            if done:
                print(f"{name} {goals}: already labelled")
                continue
            print(f"{name} {goals}: judging {len(poi_ids)} POIs", flush=True)
            labels = judge_query(fixture, goals)
            out["queries"] = [q for q in out["queries"] if q["goals"] != goals]
            out["queries"].append({"goals": goals, "labelled_at": datetime.now(timezone.utc).isoformat(),
                                   "labels": labels})
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False))


def sample(per_query: int = 8) -> None:
    """Print the label histogram and a few places at each end of the scale, for a human to eyeball."""
    rng = random.Random(1)
    for path in sorted(LABEL_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        pois = {str(p["id"]): p for p in json.loads((FIXTURE_DIR / path.name).read_text())["pois"]}
        for q in data["queries"]:
            hist = [sum(1 for v in q["labels"].values() if v == level) for level in range(5)]
            print(f"\n{data['fixture']}  goals={q['goals']}  n={len(q['labels'])}  labels 0..4: {hist}")
            items = list(q["labels"].items())
            rng.shuffle(items)
            items.sort(key=lambda kv: -kv[1])
            for pid, label in items[:per_query // 2] + items[-per_query // 2:]:
                p = pois[pid]
                print(f"   {label}  {p['name'][:40]:<40} {p['category']:<10} "
                      f"{p['tags'].get('cuisine') or p['tags'].get('tourism') or p['tags'].get('amenity') or ''}")


def audit(pairs_per_city: int = BATCH_SIZE) -> None:
    """
    Test-retest: judge a random batch per city a second time, in a different
    order and different company, and compare with the stored labels.
    """
    rng = random.Random(2)
    exact = within_one = total = 0
    for path in sorted(LABEL_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        fixture = json.loads((FIXTURE_DIR / path.name).read_text())
        q = rng.choice(data["queries"])
        batch = rng.sample(fixture["pois"], min(pairs_per_city, len(fixture["pois"])))
        again = judge_batch(fixture, q["goals"], batch)
        for poi, label in zip(batch, again):
            first = q["labels"][str(poi["id"])]
            exact += first == label
            within_one += abs(first - label) <= 1
            total += 1
        print(f"  {data['fixture']} {q['goals']}: rejudged {len(batch)}", flush=True)
    print(f"judge test-retest on {total} pairs: exact agreement {exact / total:.1%}, "
          f"within one grade {within_one / total:.1%}")


def second_opinion(model: str, city: str = "paris_marais") -> None:
    """
    Inter-judge agreement: have a different model grade one whole query group
    (the city's first) with the same prompt, and compare with the stored labels.
    The second judge's labels are saved under labels/audit/, never used for training.
    """
    from scipy.stats import spearmanr

    stored = json.loads((LABEL_DIR / f"{city}.json").read_text())["queries"][0]
    fixture = json.loads((FIXTURE_DIR / f"{city}.json").read_text())
    other = judge_query(fixture, stored["goals"], model=model)
    ids = sorted(stored["labels"])
    a = [stored["labels"][i] for i in ids]
    b = [other[i] for i in ids]
    result = {
        "fixture": city, "goals": stored["goals"], "judge_a": JUDGE_MODEL, "judge_b": model, "n": len(ids),
        "exact_agreement": sum(x == y for x, y in zip(a, b)) / len(ids),
        "within_one_grade": sum(abs(x - y) <= 1 for x, y in zip(a, b)) / len(ids),
        "spearman": float(spearmanr(a, b)[0]),
        "judge_b_labels": other,
    }
    (LABEL_DIR / "audit").mkdir(exist_ok=True)
    (LABEL_DIR / "audit" / f"second_opinion_{city}.json").write_text(json.dumps(result, indent=2))
    print(f"{JUDGE_MODEL} vs {model} on {city} {stored['goals']} (n={len(ids)}): "
          f"exact {result['exact_agreement']:.1%}, within one grade {result['within_one_grade']:.1%}, "
          f"Spearman {result['spearman']:.3f}")


if __name__ == "__main__":
    if "--sample" in sys.argv:
        sample()
    elif "--audit" in sys.argv:
        audit()
    elif "--second-opinion" in sys.argv:
        second_opinion(sys.argv[sys.argv.index("--second-opinion") + 1])
    else:
        label_all()
