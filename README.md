# roam

An AI-powered travel itinerary generator that goes beyond LLM wrappers.

## ML Pipeline

```
User Input
    ↓
[RAG Retrieval] ← Vector DB of scraped travel content (FAISS + sentence-transformers)
    ↓
[POI Fetcher] ← OpenStreetMap / Overpass API
    ↓
[Preference Ranker] ← Trained learning-to-rank model
    ↓
[VRP Optimizer] ← OR-Tools constrained route optimization
    ↓
[LLM] ← Groq (Llama 3.3 70B) synthesizes optimized route into natural itinerary
    ↓
Mobile App (React Native + Mapbox)
```

## Stack

| Layer | Tech |
|---|---|
| Mobile | React Native (Expo) |
| Backend | Python + FastAPI |
| Embeddings | sentence-transformers |
| Vector Store | FAISS |
| LLM | Groq (Llama 3.3 70B) |
| Route Optimization | OR-Tools (VRP) |
| POI Data | OpenStreetMap / Overpass API |
| Maps | Mapbox |
| Routing/Times | OpenRouteService |
| Geocoding | Nominatim |

## Structure

```
roam/
├── backend/
│   ├── api/          # FastAPI route handlers
│   ├── ml/
│   │   ├── rag/      # RAG pipeline (scraping, chunking, embedding, retrieval)
│   │   ├── ranker/   # Learning-to-rank POI scorer
│   │   └── optimizer/# VRP constrained route optimizer
│   ├── services/     # Overpass, ORS, Nominatim, Groq clients
│   └── main.py
├── mobile/
│   ├── app/          # Expo Router screens
│   ├── components/   # UI components
│   └── services/     # API calls to backend
└── data/             # Processed datasets, embeddings
```

## Setup

Coming soon.
