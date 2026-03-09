"""
Query-time retrieval: embed user query, search FAISS, return context.
"""

from .embedder import embed_query
from .vector_store import load_index, search

_index = None
_metadata = None


def _load():
    global _index, _metadata
    if _index is None:
        _index, _metadata = load_index()


def retrieve(query: str, destination: str, top_k: int = 5) -> list[dict]:
    """
    Retrieve top-k relevant chunks for a user query + destination.
    Filters results to the relevant destination where possible.
    """
    _load()
    query_vec = embed_query(f"{destination} {query}")
    results = search(_index, _metadata, query_vec, top_k=top_k * 2)

    # prefer chunks from the target destination
    destination_results = [r for r in results if destination.lower() in r["destination"].lower()]
    other_results = [r for r in results if destination.lower() not in r["destination"].lower()]

    combined = (destination_results + other_results)[:top_k]
    return combined


def format_context(chunks: list[dict]) -> str:
    """Format retrieved chunks into a single context string for the LLM."""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"[Source {i} - {chunk['destination']}]\n{chunk['text']}")
    return "\n\n".join(parts)
