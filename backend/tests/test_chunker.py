from backend.ml.rag.chunker import chunk_text, chunk_file
from pathlib import Path
import tempfile


def test_chunk_text_basic():
    text = " ".join([f"word{i}" for i in range(1000)])
    chunks = chunk_text(text, chunk_size=512, overlap=64)
    assert len(chunks) > 1
    # each chunk should be roughly chunk_size words
    for chunk in chunks:
        assert len(chunk.split()) <= 512 + 10


def test_chunk_text_overlap():
    text = " ".join([f"word{i}" for i in range(600)])
    chunks = chunk_text(text, chunk_size=512, overlap=64)
    # with overlap, last words of chunk N should appear in chunk N+1
    last_words_of_first = set(chunks[0].split()[-64:])
    first_words_of_second = set(chunks[1].split()[:64])
    assert len(last_words_of_first & first_words_of_second) > 0


def test_chunk_text_short_input():
    # chunks under 50 words are skipped by design
    text = "This is a short text."
    chunks = chunk_text(text)
    assert len(chunks) == 0


def test_chunk_text_sufficient_length():
    text = " ".join(["word"] * 60)
    chunks = chunk_text(text)
    assert len(chunks) == 1


def test_chunk_file(tmp_path):
    f = tmp_path / "tokyo.txt"
    f.write_text("word " * 600)
    chunks = chunk_file(f)
    assert len(chunks) > 0
    assert chunks[0]["destination"] == "Tokyo"
    assert "text" in chunks[0]
    assert "chunk_id" in chunks[0]
