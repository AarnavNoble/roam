---
title: Roam
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---

# roam

An AI-powered travel itinerary generator. Give it a destination, trip duration, transport mode, and your interests — it returns a day-by-day itinerary with stops ordered to minimize travel time.

Most "AI" travel apps are LLM wrappers: prompt GPT, display output. Roam builds the actual ML stack underneath.

---

## How it works

```
User Input (destination, days, transport, goals)
         │
         ▼
 ┌─────────────────┐
 │  RAG Retrieval  │  ← FAISS vector search over scraped Wikivoyage + Reddit content
 │                 │     sentence-transformers (all-MiniLM-L6-v2) embeddings
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │   POI Fetcher   │  ← Overpass API (OpenStreetMap) — local places only, chains filtered
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │ Preference      │  ← LightGBM LambdaRank model trained on (goal, POI, relevance) triplets
 │ Ranker          │     8 features: semantic similarity + category match signals
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │ VRP Optimizer   │  ← OR-Tools TSP with time windows — minimizes daily travel time
 │                 │     Assigns POIs across days, respects 10hr daily budget
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │  LLM Synthesis  │  ← Groq (Llama 3.3 70B) generates natural language itinerary
 │                 │     from optimized route + retrieved travel context
 └────────┬────────┘
          │
          ▼
  Mobile App (React Native + MapLibre)
```

---

## ML Components

### 1. RAG Pipeline (`backend/ml/rag/`)
Retrieval-Augmented Generation over real travel content — not just prompting an LLM blind.

- Scrapes Wikivoyage travel guides + Reddit trip reports per destination
- Chunks text into overlapping 512-word windows
- Embeds with `sentence-transformers/all-MiniLM-L6-v2` (384-dim, runs locally)
- Stores in FAISS flat index (cosine similarity via inner product on normalized vectors)
- At query time, retrieves top-5 semantically relevant chunks to ground the LLM

### 2. Learning-to-Rank (`backend/ml/ranker/`)
A trained model that scores POIs against user goals — not keyword matching.

- **Features**: cosine similarity between goal embedding and POI description, category match signals (food/nature/history/nightlife), name specificity, tag richness
- **Model**: LightGBM with `lambdarank` objective — the same ranking approach used in production search engines (NDCG-optimized)
- **Training data**: synthetic (goal, POI list, relevance scores) scenarios covering 5 travel styles
- **Feedback hook**: stubbed for online learning — thumbs up/down signals can trigger incremental retraining

### 3. VRP Route Optimizer (`backend/ml/optimizer/`)
Formulates itinerary generation as a constrained Vehicle Routing Problem — not just sorting by distance.

- Builds NxN travel time matrix (OpenRouteService API, Haversine fallback)
- Solves TSP per day using OR-Tools with time windows (opening hours) and visit duration constraints
- Greedy day assignment: spreads ranked POIs across trip days respecting 10-hour daily budget
- Returns estimated arrival times per stop

---

## Stack

| Layer | Tech |
|---|---|
| Mobile | React Native (Expo) + MapLibre |
| Backend | Python + FastAPI |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2`) |
| Vector Store | FAISS |
| Ranking | LightGBM LambdaRank |
| Route Optimization | Google OR-Tools (TSP/VRP) |
| LLM | Groq API (Llama 3.3 70B) |
| POI Data | OpenStreetMap / Overpass API |
| Routing | OpenRouteService |
| Geocoding | Nominatim |

Everything except Groq is free and open source. Groq has a free tier.

---

## Project Structure

```
roam/
├── backend/
│   ├── api/
│   │   └── routes.py           # FastAPI endpoints — wires full pipeline
│   ├── ml/
│   │   ├── rag/
│   │   │   ├── scraper.py      # Wikivoyage + Reddit scraper
│   │   │   ├── chunker.py      # Overlapping text chunker
│   │   │   ├── embedder.py     # sentence-transformers encoding
│   │   │   ├── vector_store.py # FAISS index build/save/load
│   │   │   ├── retriever.py    # Query-time retrieval
│   │   │   └── build_pipeline.py # One-shot index builder
│   │   ├── ranker/
│   │   │   ├── features.py     # Feature extraction (embeddings + metadata)
│   │   │   ├── model.py        # LightGBM LambdaRank model
│   │   │   ├── trainer.py      # Training on synthetic data
│   │   │   └── scorer.py       # Runtime scoring + feedback hook
│   │   └── optimizer/
│   │       ├── distance.py     # Travel time matrix (ORS + Haversine fallback)
│   │       ├── vrp.py          # OR-Tools TSP solver with time windows
│   │       └── scheduler.py    # Day assignment + route optimization
│   ├── services/
│   │   ├── overpass.py         # OSM POI fetcher (chains filtered)
│   │   ├── nominatim.py        # Geocoding
│   │   └── groq_client.py      # LLM synthesis
│   └── main.py
├── mobile/
│   ├── app/
│   │   ├── index.tsx           # Home screen (trip input)
│   │   └── itinerary.tsx       # Results screen (list + map view)
│   └── services/
│       └── api.ts              # Typed API client
└── data/                       # FAISS index + trained model (gitignored)
```

---

## Setup

### Backend

```bash
cd roam
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# Add your Groq API key (free at console.groq.com)
cp backend/.env.example backend/.env
# Edit backend/.env and set GROQ_API_KEY

# Build RAG index (scrapes + embeds ~8 cities, takes ~5 min)
python -m backend.ml.rag.build_pipeline

# Train the ranker
python -m backend.ml.ranker.trainer

# Start the API
uvicorn backend.main:app --reload
```

### Mobile

```bash
cd mobile
npm install
cp .env.example .env
npx expo start
```

Scan the QR code with **Expo Go** (iOS / Android). Phone and Mac must be on the same WiFi.

---

## API

### `POST /api/itinerary`

```json
{
  "destination": "Tokyo",
  "days": 3,
  "transport": "walking",
  "goals": ["food", "history", "hidden gems"]
}
```

Returns a structured day-by-day itinerary with stops, arrival times, descriptions, and coordinates.

### `POST /api/feedback`

```json
{
  "poi_id": 12345,
  "relevant": true
}
```

Logs positive/negative signals for future ranker retraining.
