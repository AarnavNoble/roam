"""
One-time script to scrape, chunk, embed, and index a list of destinations.
Run this to populate the FAISS vector store before serving the API.

Usage:
    python -m backend.ml.rag.build_pipeline
"""

from .scraper import scrape_and_save
from .chunker import chunk_file
from .embedder import embed_texts
from .vector_store import build_index, save_index
from pathlib import Path
import numpy as np

RAW_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "raw"

# Add more destinations here as needed
DESTINATIONS = [
    "Tokyo",
    "Paris",
    "New York City",
    "Bangkok",
    "Barcelona",
    "London",
    "Rome",
    "Sydney",
    "Toronto",
    "San Francisco",
]


def run():
    all_chunks = []

    # Step 1: scrape
    for dest in DESTINATIONS:
        out_path = RAW_DATA_DIR / f"{dest.lower().replace(' ', '_')}.txt"
        if not out_path.exists():
            scrape_and_save(dest)
        else:
            print(f"Skipping scrape for {dest} (already exists)")

    # Step 2: chunk all raw files
    for f in RAW_DATA_DIR.glob("*.txt"):
        chunks = chunk_file(f)
        all_chunks.extend(chunks)
        print(f"{f.name}: {len(chunks)} chunks")

    print(f"\nTotal chunks: {len(all_chunks)}")

    # Step 3: embed
    texts = [c["text"] for c in all_chunks]
    embeddings = embed_texts(texts)

    # Step 4: build + save FAISS index
    index = build_index(embeddings)
    save_index(index, all_chunks)
    print("Pipeline complete.")


if __name__ == "__main__":
    run()
