"""
Embeds text chunks using sentence-transformers (all-MiniLM-L6-v2).
Runs fully locally, no API key needed.
"""

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


def embed_texts(texts: list[str]) -> np.ndarray:
    """
    Embed a list of strings. Returns (N, 384) float32 array.
    """
    model = get_model()
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=True, normalize_embeddings=True)
    return embeddings.astype(np.float32)


def embed_query(query: str) -> np.ndarray:
    """
    Embed a single query string. Returns (384,) float32 array.
    """
    model = get_model()
    embedding = model.encode([query], normalize_embeddings=True)
    return embedding[0].astype(np.float32)
