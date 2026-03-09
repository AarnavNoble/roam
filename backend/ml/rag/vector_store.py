"""
FAISS vector store: build, save, and load the index.
"""

import faiss
import numpy as np
import pickle
from pathlib import Path

INDEX_DIR = Path(__file__).resolve().parents[4] / "data"
INDEX_PATH = INDEX_DIR / "faiss.index"
METADATA_PATH = INDEX_DIR / "chunks_meta.pkl"


def build_index(embeddings: np.ndarray) -> faiss.Index:
    """Build a FAISS flat inner-product index (cosine sim since embeddings are normalized)."""
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)  # inner product = cosine sim for normalized vectors
    index.add(embeddings)
    return index


def save_index(index: faiss.Index, metadata: list[dict]) -> None:
    faiss.write_index(index, str(INDEX_PATH))
    with open(METADATA_PATH, "wb") as f:
        pickle.dump(metadata, f)
    print(f"Saved index ({index.ntotal} vectors) to {INDEX_PATH}")


def load_index() -> tuple[faiss.Index, list[dict]]:
    if not INDEX_PATH.exists():
        raise FileNotFoundError("FAISS index not found. Run build_pipeline.py first.")
    index = faiss.read_index(str(INDEX_PATH))
    with open(METADATA_PATH, "rb") as f:
        metadata = pickle.load(f)
    return index, metadata


def search(index: faiss.Index, metadata: list[dict], query_vec: np.ndarray, top_k: int = 5) -> list[dict]:
    """Return top-k chunks most similar to query_vec."""
    scores, indices = index.search(query_vec.reshape(1, -1), top_k)
    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        result = metadata[idx].copy()
        result["score"] = float(score)
        results.append(result)
    return results
