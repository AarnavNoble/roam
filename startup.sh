#!/bin/bash
# Start the API. Used by the Docker image (PORT=7860 there), the Procfile and
# nixpacks. Migrations are applied by the app itself on startup
# (backend/main.py), so they are not run here.
#
# The API can serve without the two model artifacts, in a degraded mode it
# reports on every response, so neither of them is allowed to stop it starting.
set -u

echo "=== roam startup ==="
mkdir -p data

# Ranker model. Trained from fixtures in the repo, about a minute, no network
# beyond the embedding model download. The Docker image already has it.
# Without it stops are ranked by embedding similarity ("ranker:semantic").
if [ ! -f data/ranker_model.pkl ]; then
  echo "No ranker model found, training one..."
  python -m backend.ml.ranker.trainer \
    || echo "WARNING: ranker training failed. Serving with ranker:semantic."
fi

# Retrieval index. Building it scrapes Wikivoyage and Reddit, which takes
# minutes and fails whenever they are down, so it runs in the background and
# the API starts straight away. The retriever looks for the index again on
# each request, so it is picked up as soon as it has been written. Until then
# responses carry "rag:unavailable".
if [ ! -f data/faiss.index ]; then
  echo "WARNING: no RAG index (data/faiss.index). Building it in the background;"
  echo "         itineraries are served without retrieved context until it is done."
  (
    if python -m backend.ml.rag.build_pipeline; then
      echo "RAG index built."
    else
      rm -f data/faiss.index data/chunks_meta.pkl
      echo "WARNING: RAG index build failed. Still serving with rag:unavailable; restart to retry."
    fi
  ) &
fi

echo "Starting API on port ${PORT:-8000}..."
# One worker on purpose: rate limits, circuit breakers and the POI cache live
# in process memory. exec so the server receives SIGTERM directly.
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
