#!/bin/bash
set -e

echo "=== roam startup ==="

# Train ranker if model doesn't exist
if [ ! -f "data/ranker_model.pkl" ]; then
  echo "Training ranker..."
  python -m backend.ml.ranker.trainer
fi

# Build RAG index if it doesn't exist
if [ ! -f "data/faiss.index" ]; then
  echo "Building RAG index..."
  python -m backend.ml.rag.build_pipeline
fi

echo "Starting API..."
uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
