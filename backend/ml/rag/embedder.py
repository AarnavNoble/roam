"""
Embeds text chunks using sentence-transformers (all-MiniLM-L6-v2).
Runs fully locally, no API key needed.
"""

import threading

from sentence_transformers import SentenceTransformer
import numpy as np

MODEL_NAME = "all-MiniLM-L6-v2"  # 384-dim, fast, good quality
_model = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        print(f"Loading embedding model: {MODEL_NAME}")
        _model = SentenceTransformer(MODEL_NAME)
    return _model


# POI descriptions repeat heavily across requests (the same neighbourhoods get
# asked about again and again), and encoding is the most expensive CPU step in
# ranking. 20k vectors of 384 float32 is about 30 MB.
_CACHE_MAX = 20_000
_cache: dict[str, np.ndarray] = {}
_cache_lock = threading.Lock()
cache_stats = {"hits": 0, "misses": 0}


def embed_texts(texts: list[str]) -> np.ndarray:
    """
    Embed a list of strings. Returns (N, 384) float32 array.
    Only texts not seen before are sent through the model.
    """
    with _cache_lock:
        missing = list(dict.fromkeys(t for t in texts if t not in _cache))
        cache_stats["hits"] += len(texts) - len(missing)
        cache_stats["misses"] += len(missing)
    if missing:
        vectors = get_model().encode(missing, batch_size=64, show_progress_bar=False,
                                     normalize_embeddings=True).astype(np.float32)
        with _cache_lock:
            if len(_cache) + len(missing) > _CACHE_MAX:
                _cache.clear()
            _cache.update(zip(missing, vectors))
    with _cache_lock:
        # a concurrent clear() between the two locks can drop entries; re-encode those
        absent = [t for t in texts if t not in _cache]
        if not absent:
            return np.stack([_cache[t] for t in texts]) if texts else np.zeros((0, 384), np.float32)
    return get_model().encode(texts, batch_size=64, show_progress_bar=False,
                              normalize_embeddings=True).astype(np.float32)


def embed_query(query: str) -> np.ndarray:
    """
    Embed a single query string. Returns (384,) float32 array.
    """
    return embed_texts([query])[0]
