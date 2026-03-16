import numpy as np
from backend.ml.rag.embedder import embed_texts, embed_query


def test_embed_texts_shape():
    texts = ["Tokyo food scene", "Paris history", "London nightlife"]
    embeddings = embed_texts(texts)
    assert embeddings.shape == (3, 384)
    assert embeddings.dtype == np.float32


def test_embed_query_shape():
    vec = embed_query("local food and hidden gems")
    assert vec.shape == (384,)
    assert vec.dtype == np.float32


def test_embeddings_normalized():
    texts = ["some travel text"]
    embeddings = embed_texts(texts)
    norm = np.linalg.norm(embeddings[0])
    assert abs(norm - 1.0) < 1e-5


def test_similar_texts_closer_than_different():
    food1 = embed_query("best restaurants and street food")
    food2 = embed_query("local cuisine and dining spots")
    history = embed_query("ancient ruins and historic monuments")

    sim_food = float(np.dot(food1, food2))
    sim_diff = float(np.dot(food1, history))
    assert sim_food > sim_diff
