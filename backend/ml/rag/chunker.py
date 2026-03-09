"""
Splits raw destination text into overlapping chunks for embedding.
"""

from pathlib import Path


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """
    Split text into overlapping word-level chunks.
    chunk_size: target words per chunk
    overlap: words to repeat between adjacent chunks
    """
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if len(chunk.strip()) > 50:  # skip tiny chunks
            chunks.append(chunk)
        start += chunk_size - overlap

    return chunks


def chunk_file(file_path: Path) -> list[dict]:
    """
    Chunk a raw text file. Returns list of dicts with text + metadata.
    """
    text = file_path.read_text(encoding="utf-8")
    destination = file_path.stem.replace("_", " ").title()
    chunks = chunk_text(text)

    return [
        {"text": chunk, "destination": destination, "source": file_path.name, "chunk_id": i}
        for i, chunk in enumerate(chunks)
    ]


if __name__ == "__main__":
    from pathlib import Path
    sample = Path(__file__).resolve().parents[4] / "data" / "raw"
    for f in sample.glob("*.txt"):
        chunks = chunk_file(f)
        print(f"{f.name}: {len(chunks)} chunks")
