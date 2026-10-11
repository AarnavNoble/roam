---
title: Roam
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---

# Roam

Roam builds a walkable day plan from a city, a starting point, a number of hours and a list of interests. The backend is FastAPI; the client is a React Native (Expo) app.

The route is not written by a language model. Places come from OpenStreetMap, a LightGBM ranker picks which ones to visit, and an OR-Tools solver decides the order and the arrival times. The LLM receives the finished route as a numbered list and returns only prose keyed by stop number. The server joins that prose onto the facts it already holds, so names, coordinates and times never pass through model output. If no model answers, the same itinerary is returned with text written from OSM tags.

Every stage that depends on an outside service has a fallback, and each response lists the fallbacks it used in a `degraded` field.

## Contents

- [Request path](#request-path)
- [Measured results](#measured-results)
- [Reliability](#reliability)
- [Ranking](#ranking)
- [Routing](#routing)
- [Writer contract](#writer-contract)
- [Trip-editing agent](#trip-editing-agent)
- [Observability](#observability)
- [Data model](#data-model)
- [API](#api)
- [Running it](#running-it)
- [Limitations](#limitations)

## Request path

`backend/pipeline.py` runs the stages below under one request deadline (60 s by default). Retrieval runs on a worker thread while geocoding and the POI fetch run; photos are fetched while the LLM writes.

```
POST /api/itinerary
        |
        +--> retrieve (FAISS, worker thread) ---------------------------+
        |      fallback: no context                   rag:unavailable   |
        v                                                               |
     geocode      Nominatim, then Photon             geocode:photon     |
        |         unknown place -> 400; both down -> 503                |
        v                                                               |
     fetch POIs   one batched Overpass query, mirrors hedged            |
        |         memory cache (1 h) -> live -> stored copy (7 d)       |
        |         fallback: stored copy              pois:stale         |
        |         nothing nearby -> 404; no data at all -> 503          |
        v                                                               |
     rank         LightGBM LambdaRank + per-goal diversity              |
        |         fallback: embedding cosine only    ranker:semantic    |
        v                                                               |
     route        OR-Tools, soft time-of-day windows                    |
        |         travel times: ORS, else haversine  matrix:haversine   |
        |         fallback: greedy bucket ordering   route:greedy       |
        v                                                               |
        +--> photos (Wikipedia/Wikidata, worker thread, 6 s wait)       |
        |      fallback: no photos                   photos:unavailable |
        v                                                               |
     write        LLM returns prose keyed by stop number  <-------------+
        |         fallback: next model, then text from tags  llm:fallback
        |         invalid answer repaired per stop           llm:repaired
        v
     assemble     server joins prose onto the route, stores the trip
```

### `degraded` flags

| Flag | Meaning |
|---|---|
| `geocode:photon` | Nominatim was down, Photon answered |
| `pois:stale` | every Overpass mirror was down, the stored copy was served |
| `matrix:haversine` | an ORS key is set but ORS failed, straight-line travel times were used |
| `route:greedy` | the solver returned nothing, the greedy ordering was used |
| `ranker:semantic` | no trained model could be loaded, stops were ranked by embedding similarity |
| `rag:unavailable` | no retrieval context (no index, or retrieval failed or timed out) |
| `llm:fallback` | no model answered, text was written from OSM tags |
| `llm:repaired` | the model's answer broke the contract and was repaired |
| `photos:unavailable` | photo lookup failed or ran out of time |

A request fails only when there is nothing to build from: the place does not exist (400), Overpass answered and nothing matches nearby (404), or no geocoder or no POI data, live or stored, could be reached (503 with `Retry-After`). A failed trip save does not fail the request; the itinerary is returned with `trip_id: null`.

## Measured results

All numbers come from JSON files under `backend/bench/results/`. `TBD(...)` marks a benchmark that has not been run on the current code.

### End-to-end latency

Live upstreams, nothing mocked: 8 city scenarios, 2 trials each, 16 requests.

```bash
python -m backend.bench.latency_bench --label baseline --trials 2
```

| | Requests | Median | p95 |
|---|---|---|---|
| Before the rework (`latency_baseline.json`) | 16 (15 completed) | 40.7 s | 59.6 s |
| After | `TBD(latency_after)` | `TBD(latency_after)` | `TBD(latency_after)` |

Where the time went before the rework:

| Stage | p50 | p95 | Share of total time |
|---|---|---|---|
| fetch_pois | 33.8 s | 50.3 s | 81.9% |
| llm | 3.7 s | 6.9 s | 10.6% |
| rank | 1.2 s | 8.5 s | 6.5% |
| geocode | 2 ms | 0.6 s | 0.4% |
| retrieve | 66 ms | 0.5 s | 0.4% |
| route | 4 ms | 39 ms | 0.0% |

The POI fetch made one Overpass round trip per category, and each round trip first waited out a failing mirror. What changed:

- One batched Overpass query covers every category, with a per-category result limit.
- Mirrors are raced with a hedge delay and each has its own circuit breaker.
- Raw Overpass results are cached in memory, and a stored copy is served stale during an outage.
- Embeddings are cached by text, so repeated POI descriptions are not re-encoded.
- Retrieval runs in parallel with geocoding and the POI fetch. Photos run in parallel with the LLM.
- The LLM writes prose keyed by stop number and no longer echoes every stop as JSON.

### Prompt format

Real model answers for both prompt formats on the same 10 routes, recorded once and replayed offline.

```bash
python -m backend.bench.contract_eval --record   # live, needs GROQ_API_KEY
python -m backend.bench.contract_eval --check    # offline replay, runs in CI
```

| Format | Prompt tokens | Completion tokens | Seconds per itinerary | Answers breaking the contract |
|---|---|---|---|---|
| Echo (model repeats every stop as JSON) | 5,240 | 990 | 2.6 | 0 of 10 |
| Keyed (prose by stop number) | 1,122 | 510 | 1.5 | 0 of 10 |

The keyed format uses 74% fewer tokens. Neither format broke the contract in this sample, so the sample does not show the validator catching a bad answer. The reason for the keyed format is structural: the model cannot change a name, a coordinate or a time it never outputs. `--check` also exits non-zero if any assembled itinerary differs from its route in a stop, coordinate or time.

### Ranker

Leave-one-city-out cross validation over 8 cities, against two baselines (random order, and cosine similarity alone). Labels are 0 to 4 relevance grades from an LLM judge.

```bash
python -m backend.bench.ranker_bench
python -m backend.bench.ranker_bench --check   # CI gate: model must beat cosine_only on mean NDCG@10
```

| Model | NDCG@10, full pool | NDCG@10, goal pool |
|---|---|---|
| random | `TBD(ranker)` | `TBD(ranker)` |
| cosine_only | `TBD(ranker)` | `TBD(ranker)` |
| lambdarank | `TBD(ranker)` | `TBD(ranker)` |

### Route ordering

Five orderings of the same sampled days (4, 6 and 9 stops), scored on minutes of travel and minutes outside each stop's preferred time window.

```bash
python -m backend.bench.route_bench --sweep
```

| Strategy | Travel (min) | Off-window (min) |
|---|---|---|
| rank_order | `TBD(route)` | `TBD(route)` |
| greedy_nn | `TBD(route)` | `TBD(route)` |
| time_aware (shipped before) | `TBD(route)` | `TBD(route)` |
| ortools (ships now) | `TBD(route)` | `TBD(route)` |
| ortools_free (no windows, lower bound on travel) | `TBD(route)` | `TBD(route)` |

### Trip-editing agent

Each case is a plain-language request against a trip plus a programmatic check on the stop set before and after. The model is live; the places are frozen fixtures.

```bash
python -m backend.bench.agent_eval
```

Result: `TBD(agent_eval)`.

### Fault injection and tests

`backend/tests/test_faults.py` sends requests through the real HTTP endpoint with every upstream faked, breaks one thing at a time, and compares the status code and the exact `degraded` list.

```bash
python -m pytest backend/tests -q
```

Fault scenarios: `TBD(faults)`. Total tests: `TBD(tests)`.

### What the benchmarks found

- **The configured LLM model had been retired.** Every request failed at the last stage. The client now tries a list of models in order, and a 404 opens that model's breaker for an hour.
- **The main Overpass server answers 406 to requests with no User-Agent.** The mirror loop swallowed the error, so every city returned "no places found". The client now sends a User-Agent, and an upstream failure is reported as 503, not as an empty result.
- **The original ranker did not rank.** It was trained on 35 hand-written rows with LightGBM defaults that cannot split 35 rows, and it scored the same as random order (NDCG@3 0.619, against 0.988 for cosine similarity alone). It was rebuilt on labelled OpenStreetMap candidate pools and is now gated in CI against the cosine baseline.
- **82% of request time was the POI fetch.** The LLM, which looked like the obvious suspect, was 11%.

## Reliability

The primitives are in `backend/core/resilience.py` and are shared by every upstream client.

| Mechanism | What it does | Why |
|---|---|---|
| Deadline | One wall-clock budget per request (`REQUEST_DEADLINE_S`, default 60). Each call's timeout is capped by what is left. | A slow stage shortens the stages after it, so the total stays bounded. |
| Retry with jitter | Bounded attempts. The wait before a retry is drawn uniformly from 0 to an exponentially growing cap. | Clients retrying together do not hit the upstream at the same instant. |
| Circuit breakers | One per Overpass mirror, per LLM model, per geocoder, and one for ORS. Opens after consecutive failures, lets one probe through after a reset period. | A dead upstream costs nothing after its first failures. If every Overpass breaker is open, mirrors are probed anyway, because refusing would fail the request for certain. |
| Hedged mirrors | The first Overpass mirror is called alone. If it has not answered in 4 s, or it fails, the next one starts. First success wins. Up to 3 rounds with backoff. | A slow or erroring mirror no longer decides how long the caller waits. |
| Stale cache | Raw Overpass results are cached in memory for 1 hour (keyed on a roughly 100 m grid) and written to the `poi_cache` table. When every mirror is down, a stored copy up to 7 days old is served and flagged `pois:stale`. | Places change slowly. An old list is better than a 503. |
| Model failover | `GROQ_MODELS` is tried in order. An API error, rate limit or timeout moves to the next model. An answer that is not a JSON object goes straight to the deterministic writer. Running out of the request's own deadline is not counted against a model. | One retired or rate-limited model costs one failed call, not the request. |
| Deterministic writer | `writer.fallback_raw` produces an answer in the model's output shape from OSM tags alone. It is used whole when no model answers, and per stop when the model skips or mangles one. | The response is always a complete itinerary. |
| Rate limiting | Per-client token buckets, keyed on `X-Forwarded-For`. Itinerary: burst of 6, then one per 20 s. Edit: 5, then one per 15 s. Auth: 10, then one per 30 s. Empty bucket returns 429 with `Retry-After`. | LLM tokens and Overpass quota are shared by all users. |

`GET /api/health` reports the database and the state of every breaker. It answers 200 while the process can serve, including when a dependency is down.

## Ranking

`backend/ml/ranker/`

- **Model.** LightGBM with a `lambdarank` objective, 150 trees, 15 leaves, `min_child_samples=20`. Parameters were fixed before the benchmark was run and are not tuned on its folds.
- **Features (14).** Cosine similarity between the goals and the POI description (`all-MiniLM-L6-v2`), that score's percentile within the request, a category match flag plus one flag per goal and category pair, and four signals of an established place: a Wikipedia or Wikidata tag, tag count, a website or opening hours, a `tourism=*` tag.
- **Training data.** OpenStreetMap candidate pools for 8 city neighbourhoods (`backend/bench/fixtures/`), each judged against 3 goal sets on a 0 to 4 scale by an LLM judge (`backend/bench/label_pois.py`). The trainer reads the labels from `backend/bench/labels/`. Two cities are held out and never trained on.
- **Selection.** After scoring, each goal is guaranteed its best-ranked stop, and the remaining slots are filled by score with a per-category cap.
- **Explanations.** Each response carries per-stop feature values and SHAP contributions (`ranking_explanations`) and global feature importance.
- **Feedback loop.** `POST /api/feedback` stores a thumbs up or down. Every 10 new signals a candidate is trained on the base set plus all feedback, then scored against the serving model on the two held-out cities. It is promoted only if its NDCG@10 is within 0.01 of the incumbent or better. Every run, promoted or rejected, is written to `model_versions`. The model file is replaced atomically.
- **Fallback.** With no loadable model, stops are ordered by the cosine feature alone and the response is flagged `ranker:semantic`.

## Routing

`backend/ml/optimizer/`

1. **Travel times.** OpenRouteService matrix when `ORS_API_KEY` is set, otherwise straight-line distance over an average speed per transport mode.
2. **Day assignment.** Ranked stops are assigned greedily to days against a 10 hour budget.
3. **Ordering.** `vrp.solve_day` models each day as an open path from the traveller's start location (a dummy end node turns OR-Tools' closed tour into a path). The objective is travel time plus a penalty for each second a stop is reached outside its preferred window: nature in the morning, museums in the afternoon, nightlife in the evening, food stops spread across meal times. The solver runs guided local search for 0.25 s per day.
4. **Trim.** If a day runs past the time the traveller has, the lowest-ranked stop is dropped and the day is solved again.

**Why soft windows.** The first version used hard time windows. A packed day had no feasible solution, so the windows were dropped and a greedy heuristic shipped in its place. With soft windows a stop outside its window is allowed and costs one travel-second per second, so there is always a feasible answer and the solver trades walking against timing. The greedy heuristic remains as the fallback (`route:greedy`).

## Writer contract

`backend/services/writer.py`

The prompt lists the stops as `[1] 09:20 Name (category, 60 min) tags -> 7 min to next` and asks for this shape:

```json
{
  "overview": "...",
  "days": [{"day": 1, "theme": "...", "summary": "..."}],
  "stops": [{"id": 1, "description": "...", "tip": "..."}],
  "excluded": []
}
```

`validate` returns a list of violations: `bad_shape`, `unknown_stop`, `duplicate_stop`, `empty_description`, `missing_stop`, `missing_overview`, `illegal_exclusion`. `assemble` then builds the itinerary from the route, taking only prose from the model. A stop with no usable prose gets the deterministic description. The model may exclude stops only when the traveller wrote notes, and never more than half of them. Any violation is reported as `llm:repaired`.

## Trip-editing agent

`backend/agent/`, exposed as `POST /api/trips/{trip_id}/edit` with a body like `{"message": "swap the museum for a park"}`.

A plain loop with no framework: one message list, one dict of tool functions. The model sees stops as `s1, s2...` and search results as `p1, p2...`, never coordinates or OSM ids.

| Tool | Effect |
|---|---|
| `list_stops` | current stops in visit order |
| `remove_stop` | remove one stop |
| `search_places` | fetch and rank up to 5 nearby places in a category that are not in the trip |
| `add_stop` | add a place returned by a search in this edit |
| `replace_stop` | swap a stop for a place returned by a search |
| `finish` | end the edit with a one-sentence summary |

Guardrails are enforced by the session, not by the prompt:

- A place can be added only if a search in this session returned it.
- The trip cannot be emptied. An edit that tries is rejected whole.
- A stop is added only if the scheduler can still fit every stop into the trip's days.
- A diet word in a search query becomes the same hard tag filter the pipeline uses.
- Bad arguments come back to the model as an `Error: ...` string. A tool call never raises.
- The loop is bounded: 6 model turns, 6 tool calls per turn, and the request deadline.
- The request and tool results are wrapped as data, and the system prompt tells the model not to follow instructions inside them.

After the loop, the edited stop set goes through the same route and write stages as a new trip. The result is saved as a new row with `parent_id` pointing at the original, so an edit never overwrites a trip. Editing needs a model to interpret the request, so with no model available the endpoint answers 503.

## Observability

`backend/core/telemetry.py`

- Every pipeline stage runs in an OpenTelemetry span. Nested spans cover the upstream calls (`overpass.query`, `geocode.lookup`, `llm.call`, `route.solve`, `route.ors_matrix`, `agent.turn`, `agent.tool`). LLM spans record the model and token usage.
- An in-process span processor keeps the last 500 durations per span name. `GET /api/metrics/latency` returns count, p50, p95 and max per stage with no collector running. The latency benchmark reads the same spans.
- Each response carries its own stage timings in `timings_ms`.
- Setting `OTEL_EXPORTER_OTLP_ENDPOINT` adds an OTLP/HTTP exporter, so traces go to any collector (Jaeger, Tempo).
- `GET /api/metrics` returns the ranker's retraining history with the gate verdict on each run.

## Data model

SQLAlchemy models in `backend/db/models.py`, schema managed by Alembic (`backend/db/migrations/`). Migrations are applied at startup.

| Table | Contents |
|---|---|
| `users` | email and bcrypt password hash |
| `trips` | the request, the structured route, the itinerary, the `degraded` list, optional `user_id`, `parent_id` (set on edits) and `share_slug` |
| `feedback` | thumbs up or down on a stop, with the goals it was shown for |
| `poi_cache` | last good Overpass answer per query key |
| `model_versions` | one row per retraining run: candidate metrics, incumbent metrics, promoted or rejected, and why |

## API

All routes are under `/api`. Auth is a bearer JWT (HS256) from register or login. Most routes accept anonymous requests.

| Method and path | Auth | Purpose |
|---|---|---|
| `POST /itinerary` | optional | run the pipeline, return the itinerary |
| `POST /itinerary/stream` | optional | same, as server-sent events: `progress` per stage, then `result` or `error` |
| `POST /trips/{id}/edit` | optional | edit a trip in plain language, returns the new itinerary |
| `GET /trips` | required | list the signed-in user's trips |
| `GET /trips/{id}` | optional | one trip. Anonymous trips are readable by id; owned trips only by their owner |
| `DELETE /trips/{id}` | required | delete an owned trip |
| `POST /trips/{id}/share` | optional | create or return a public read-only link (`/t/{slug}`) |
| `DELETE /trips/{id}/share` | required | revoke the link |
| `GET /shared/{slug}` | none | the shared itinerary, without owner or request notes |
| `POST /feedback` | optional | thumbs up or down on a stop |
| `GET /feedback/stats` | none | feedback count and signals until the next retrain |
| `POST /auth/register`, `POST /auth/login` | none | return `{token, user}` |
| `GET /auth/me` | required | the current user |
| `GET /metrics` | none | ranker versions and feature importance |
| `GET /metrics/latency` | none | p50 and p95 per stage |
| `GET /health` | none | database and breaker states |

A trip owned by someone else is reported as 404, not 403, so ids cannot be probed.

### `POST /api/itinerary`

Request. Only `city`, `start_location` and `goals` are required.

```json
{
  "city": "Paris",
  "start_location": "Le Marais",
  "duration_hours": 8,
  "goals": ["food", "history"],
  "transport": "walking",
  "pace": "moderate",
  "budget": "mid",
  "style": "solo",
  "dietary": "none",
  "mobility": "moderate",
  "familiarity": "first_time",
  "start_time": "morning",
  "notes": "",
  "trip_date": "2026-04-12"
}
```

| Field | Values |
|---|---|
| `goals` | 1 to 8 of `food`, `nature`, `history`, `culture`, `nightlife`, `shopping`, `adventure`, `hidden_gems` |
| `duration_hours` | 1 to 72. More than 10 hours becomes multiple days |
| `transport` | `walking`, `transit`, `cycling`, `driving` |
| `pace` | `relaxed` (4 stops per day), `moderate` (6), `packed` (9) |
| `dietary` | `none`, `vegetarian`, `vegan`, `halal`, `kosher`. Filters food stops on the OSM `diet:*` tag |
| `mobility` | `easy`, `moderate`, `active`. Search radius of 1.5, 2 or 3 km |
| `start_time` | `morning` (09:00), `afternoon` (13:00), `evening` (17:00) |

Response (shape only; ids, coordinates and times are illustrative, numeric metrics are zeroed, prose is elided):

```json
{
  "overview": "...",
  "days": [
    {
      "day": 1,
      "theme": "...",
      "summary": "...",
      "stops": [
        {
          "id": 123456789,
          "name": "Musée Carnavalet",
          "arrival_time": "09:12",
          "duration_min": 90,
          "description": "...",
          "tip": "...",
          "lat": 48.8576,
          "lon": 2.3622,
          "category": "history",
          "photo_url": "https://...",
          "opening_hours": "Tu-Su 10:00-18:00"
        }
      ]
    }
  ],
  "ranking_explanations": {
    "Musée Carnavalet": {"features": {"semantic_score": 0.0}, "contributions": {"semantic_score": 0.0, "bias": 0.0}}
  },
  "global_feature_importance": {"semantic_score": 0.0},
  "degraded": [],
  "timings_ms": {"geocode": 0.0, "fetch_pois": 0.0, "rank": 0.0, "route": 0.0, "llm": 0.0, "pipeline": 0.0},
  "trip_id": "9f1c..."
}
```

`id` is the OSM element id. `photo_url` and `opening_hours` are present only when known.

Errors: 400 unknown place, 404 nothing nearby, 422 validation, 429 rate limited (`Retry-After`), 503 a required data source is unavailable (`Retry-After: 30`).

## Running it

Python 3.12 is what CI and the Docker image use.

### Local, SQLite

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env      # every variable is optional

python -m backend.ml.ranker.trainer       # train the ranker from bench/fixtures + bench/labels
python -m backend.ml.rag.build_pipeline   # optional: scrape and index travel guides
uvicorn backend.main:app --reload         # http://localhost:8000
```

With no variables set the API starts, stores data in `data/roam.db`, and writes itinerary text from tags (`llm:fallback`). Without the retrieval index responses carry `rag:unavailable`. `bash startup.sh` does the same steps and builds the index in the background.

### Docker Compose, Postgres

```bash
docker compose up --build                 # API on http://localhost:7860, Postgres on host port 15434
```

Compose passes `GROQ_API_KEY`, `ORS_API_KEY` and `JWT_SECRET` through from the shell. The image trains the ranker at build time and tries to build the retrieval index; a failed scrape does not fail the build. The same Dockerfile is written for Hugging Face Spaces (uid 1000, port 7860).

### Environment variables

Read only in `backend/config.py`. See `backend/.env.example`.

| Variable | Default | Effect |
|---|---|---|
| `GROQ_API_KEY` | unset | without it, text is written from tags |
| `GROQ_MODELS` | `openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b` | models tried in order |
| `ORS_API_KEY` | unset | without it, travel times are straight-line estimates |
| `OVERPASS_MIRRORS` | four public mirrors | hedged in this order |
| `DATABASE_URL` | SQLite at `data/roam.db` | any SQLAlchemy URL, for example `postgresql+psycopg://...` |
| `JWT_SECRET` | a public dev value | must be set anywhere real accounts exist |
| `JWT_TTL_HOURS` | 720 | token lifetime |
| `REQUEST_DEADLINE_S` | 60 | wall-clock budget per request |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | enables OTLP trace export |

### Tests

```bash
python -m pytest backend/tests -q
```

Tests use a throwaway SQLite file unless `DATABASE_URL` is set, create the schema by running the real migrations, and blank the API keys so no test can reach a paid upstream. Every upstream is faked.

### CI

`.github/workflows/ci.yml` runs on every push and pull request.

| Job | Gates |
|---|---|
| backend (Postgres 16 service) | `alembic upgrade head`, `pytest backend/tests`, `ranker_bench --check` (model must beat the cosine baseline), `contract_eval --check` (assembled itineraries must match their routes) |
| mobile | `npm ci`, `npx tsc --noEmit` |

### Mobile app

Expo 52, React Native 0.76, expo-router, MapLibre. Screens: onboarding, trip input, itinerary (list and map), stop detail, history, settings. `mobile/services/api.ts` is the typed client.

```bash
cd mobile
npm install
cp .env.example .env     # set EXPO_PUBLIC_API_URL, for example http://<your-ip>:8000/api
npx expo start
```

## Limitations

- **Relevance labels come from an LLM judge.** The ranker benchmark measures agreement with that judge on unseen cities, not whether users like the itineraries. The judge saw each place's category and the model has category-match features, so part of the lift is the model learning the judge's rule. There are 8 folds.
- **Single replica.** Rate-limit buckets, circuit breakers, the POI memory cache, the embedding cache and the retrain lock live in process memory. `startup.sh` runs one worker for that reason. More replicas would need this state in a shared store.
- **Travel times are straight-line unless an ORS key is set.** ORS has no transit profile, so `transit` uses driving times as a proxy.
- **Opening hours are parsed from a subset of the OSM syntax.** Unparseable tags are treated as open. Hours filter which places are candidates; the solver does not schedule against them.
- **The time-of-day windows are fixed per category,** not learned and not per place.
- **Retrieval covers 10 cities** scraped from Wikivoyage and Reddit. For any other city the context is whatever chunks are nearest in embedding space, which may be about a different place.
- **Feedback retraining is small-data.** Thumbs are grouped by goal set and carry no OSM tags, and the gate's held-out set is two cities.
- **SQLite is the default.** Without `DATABASE_URL`, data lives in a file inside the container and is lost when the container is replaced.
- **The default `JWT_SECRET` is public** and CORS allows any origin.
- **The mobile screens are type-checked in CI but have not been tested on a device.**
- **Latency numbers are from live public upstreams** and move with their health. Compare runs taken close together.
