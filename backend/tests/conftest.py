"""
Shared test setup.

Tests run against whatever DATABASE_URL points at (CI sets Postgres). When it
is unset they get a throwaway SQLite file. Either way the schema is created by
running the real migrations, and every test starts with empty tables, empty
caches, closed circuit breakers and full rate-limit buckets.
"""

import os
import tempfile

if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='roam-test-')}/test.db"
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-anywhere-else")
# backend/.env on a developer machine holds real keys. Blank them before
# backend.config is imported so no test can spend LLM or routing quota.
os.environ["GROQ_API_KEY"] = ""
os.environ["ORS_API_KEY"] = ""

import pytest  # noqa: E402

from backend.db.models import Base  # noqa: E402
from backend.db.session import engine, run_migrations  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    run_migrations()
    yield


@pytest.fixture(autouse=True)
def _clean_state():
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())

    from backend.api import auth, routes
    from backend.ml.optimizer import distance
    from backend.services import llm, nominatim, overpass

    from backend.api import agent
    for bucket in (auth.auth_bucket, routes.itinerary_bucket, routes.feedback_bucket,
                   getattr(agent, "edit_bucket", None)):
        if bucket is None:
            continue
        bucket.reset()
    overpass._memory_cache.clear()
    overpass._breakers.clear()
    nominatim._cache.clear()
    llm._breakers.clear()
    for breaker in (nominatim._nominatim_breaker, nominatim._photon_breaker, distance._ors_breaker):
        breaker.record_success()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


# ── Fake upstreams ───────────────────────────────────────────────────────────
# Everything below is opt-in through the `upstreams` fixture. It replaces every
# outside service the pipeline talks to (Nominatim, Photon, the Overpass
# mirrors, OpenRouteService, Wikipedia, Wikidata, the LLM provider) so a test
# can break exactly one of them and look at what the API does.

import json  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
from functools import partial  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import httpx  # noqa: E402

MIRROR_A = "https://mirror-a.test/api/interpreter"
MIRROR_B = "https://mirror-b.test/api/interpreter"
MIRROR_C = "https://mirror-c.test/api/interpreter"
START = (48.8867, 2.3431)  # Montmartre, where the fake geocoders put every query
N_STOPS = 6                # stops the default request routes (one day, moderate pace)


def trip_request(**overrides) -> dict:
    """
    A valid request body. 2026-06-03 is a Wednesday, so opening hours are
    deterministic. Ten hours is long enough that all six selected stops fit in
    the day, so no test depends on which stop the scheduler would trim.
    """
    body = {"city": "Paris", "start_location": "Montmartre", "duration_hours": 10,
            "goals": ["food", "culture"], "trip_date": "2026-06-03"}
    return body | overrides


def overpass_elements() -> list[dict]:
    """What a mirror returns for the default request: 4 food, 3 culture, 3 that must be filtered."""
    def node(id_, name, lat, lon, **tags):
        return {"type": "node", "id": id_, "lat": lat, "lon": lon, "tags": {"name": name, **tags}}

    def way(id_, name, lat, lon, **tags):
        return {"type": "way", "id": id_, "center": {"lat": lat, "lon": lon}, "tags": {"name": name, **tags}}

    return [
        node(101, "Le Petit Moulin", 48.8871, 2.3388, amenity="restaurant", cuisine="french",
             opening_hours="Mo-Su 08:00-23:00"),
        node(102, "Cafe des Abbesses", 48.8845, 2.3381, amenity="cafe"),
        node(103, "Bistro Lepic", 48.8853, 2.3340, amenity="restaurant", cuisine="french",
             **{"diet:vegan": "yes"}),
        way(104, "Halle Saint-Pierre Cantine", 48.8848, 2.3443, amenity="food_court"),
        way(201, "Musee de Montmartre", 48.8877, 2.3406, tourism="museum",
            wikipedia="fr:Musée de Montmartre"),
        node(202, "Espace Dali", 48.8865, 2.3397, tourism="gallery", wikipedia="en:Dalí Paris"),
        node(203, "Theatre de l'Atelier", 48.8838, 2.3440, amenity="theatre", wikidata="Q3527158"),
        node(501, "Starbucks", 48.8850, 2.3400, amenity="cafe"),                          # chain
        {"type": "node", "id": 502, "lat": 48.8851, "lon": 2.3401, "tags": {"amenity": "cafe"}},  # no name
        node(503, "Ferme le Mercredi", 48.8852, 2.3402, amenity="restaurant", opening_hours="We off"),
    ]


def model_answer(prompt: str) -> dict:
    """What a well-behaved model returns: prose for every numbered stop in the prompt."""
    ids = [int(n) for n in re.findall(r"^\[(\d+)\]", prompt, flags=re.M)]
    return {
        "overview": "A slow morning on the hill, then galleries.",
        "days": [{"day": 1, "theme": "Hilltop Paris", "summary": "Food and art within a short walk."}],
        "stops": [{"id": i, "description": f"Model prose for stop {i}.", "tip": f"Model tip {i}."} for i in ids],
        "excluded": [],
    }


class FakeLLM:
    """
    Stands in for the Groq client. `behaviour` maps a model name to a function
    of the prompt that returns the completion (a dict is sent as JSON, a str is
    sent as is) or raises a groq error. Models not listed answer correctly.
    """

    def __init__(self):
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.behaviour: dict = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model, messages, **kwargs):
        prompt = messages[-1]["content"]
        self.calls.append(model)
        self.prompts.append(prompt)
        answer = self.behaviour.get(model, model_answer)(prompt)
        content = answer if isinstance(answer, str) else json.dumps(answer)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=len(prompt) // 4, completion_tokens=len(content) // 4),
            choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        )


def llm_error(status: int | None):
    """A completion function that fails like Groq does: an HTTP status, or a timeout for None."""
    import groq
    request = httpx.Request("POST", "https://llm.test/chat/completions")

    def fail(prompt):
        if status is None:
            raise groq.APITimeoutError(request=request)
        cls = {404: groq.NotFoundError, 429: groq.RateLimitError}.get(status, groq.InternalServerError)
        raise cls(f"HTTP {status}", response=httpx.Response(status, request=request), body=None)
    return fail


class Upstreams:
    """
    Routes httpx.get / httpx.post by URL to one handler per service and records
    every call. A handler takes (url, kwargs) and returns an httpx.Response or
    raises, exactly what the real httpx call would do. The helpers at the
    bottom build the usual failures. A URL with no handler fails the test, so
    nothing can reach the real network by accident.
    """

    def __init__(self):
        self.calls: list[str] = []
        self.requests: list[tuple[str, dict]] = []  # (service, kwargs passed to httpx)
        self.release = threading.Event()  # set at teardown so hung calls return
        self.llm = FakeLLM()
        lat, lon = START
        self.handlers = {
            "nominatim": self.json([{"lat": str(lat), "lon": str(lon)}]),
            "photon": self.json({"features": [{"geometry": {"coordinates": [lon, lat]}}]}),
            MIRROR_A: self.json({"elements": overpass_elements()}),
            MIRROR_B: self.json({"elements": overpass_elements()}),
            MIRROR_C: self.json({"elements": overpass_elements()}),
            "ors": self._ors_ok,
            "wikipedia": self.json({"thumbnail": {"source": "https://upload.test/thumb/320px-photo.jpg"}}),
            "wikidata": self.json({"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Atelier.jpg"}}}]}}),
        }

    def _service(self, url: str) -> str:
        from backend.ml.optimizer import distance
        from backend.services import nominatim
        if url in self.handlers:
            return url
        if url == nominatim.NOMINATIM_URL:
            return "nominatim"
        if url == nominatim.PHOTON_URL:
            return "photon"
        if url.startswith(distance.ORS_URL):
            return "ors"
        if ".wikipedia.org/" in url:
            return "wikipedia"
        if "wikidata.org/" in url:
            return "wikidata"
        raise AssertionError(f"test tried to reach an unfaked URL: {url}")

    def http(self, url, **kwargs) -> httpx.Response:
        service = self._service(url)
        self.calls.append(service)
        self.requests.append((service, kwargs))
        return self.handlers[service](url, kwargs)

    def count(self, service: str) -> int:
        return self.calls.count(service)

    def break_all_mirrors(self, handler=None) -> None:
        for mirror in (MIRROR_A, MIRROR_B, MIRROR_C):
            self.handlers[mirror] = handler or self.status(503)

    @staticmethod
    def _ors_ok(url, kwargs):
        n = len(kwargs["json"]["locations"])
        durations = [[abs(i - j) * 240 for j in range(n)] for i in range(n)]
        return httpx.Response(200, json={"durations": durations}, request=httpx.Request("POST", url))

    # Handler builders

    @staticmethod
    def json(payload, status: int = 200):
        return lambda url, kwargs: httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    @staticmethod
    def status(code: int, text: str = ""):
        """Answer with an HTTP status and a non-JSON body."""
        return lambda url, kwargs: httpx.Response(code, text=text, request=httpx.Request("GET", url))

    @staticmethod
    def unreachable():
        def handler(url, kwargs):
            raise httpx.ConnectError("connection refused", request=httpx.Request("GET", url))
        return handler

    def hang(self, at_most: float = 5.0):
        """Never answer: block for the caller's timeout (capped at `at_most`), then time out."""
        def handler(url, kwargs):
            self.release.wait(min(float(kwargs.get("timeout") or at_most), at_most))
            raise httpx.ReadTimeout("timed out", request=httpx.Request("GET", url))
        return handler

    def slow(self, seconds: float, then):
        """Answer correctly, but only after `seconds`."""
        def handler(url, kwargs):
            self.release.wait(seconds)
            return then(url, kwargs)
        return handler


@pytest.fixture(scope="session")
def trained_ranker():
    """
    A small LightGBM ranker fitted to synthetic rows, so tests do not need
    data/ranker_model.pkl (which is not in the repo and so not on CI).
    """
    import numpy as np
    from backend.ml.ranker.features import FEATURE_NAMES  # loads torch before lightgbm
    from backend.ml.ranker.model import POIRanker

    rng = np.random.default_rng(0)
    groups = [30] * 20
    X = rng.random((sum(groups), len(FEATURE_NAMES)))
    y = np.digitize(X[:, 0] + X[:, 1], [0.6, 1.0, 1.4]).astype(np.float32)
    ranker = POIRanker()
    ranker.train(X, y, groups)
    return ranker


@pytest.fixture(scope="session")
def rag_index():
    """A three-chunk FAISS index built with the real embedder, standing in for data/faiss.index."""
    from backend.ml.rag.embedder import embed_texts
    from backend.ml.rag.vector_store import build_index

    chunks = [
        {"destination": "Paris", "text": "Montmartre is best early, before the tour groups reach the hill."},
        {"destination": "Paris", "text": "Bistros around Rue Lepic serve a fixed-price lunch menu."},
        {"destination": "Tokyo", "text": "Shinjuku's izakaya alleys fill up after office hours."},
    ]
    return build_index(embed_texts([c["text"] for c in chunks])), chunks


@pytest.fixture
def http(monkeypatch):
    """httpx.get / httpx.post routed to fakes, all healthy. Enough for tests of one client module."""
    from backend.core.resilience import retry
    from backend.services import nominatim, overpass

    up = Upstreams()
    monkeypatch.setattr(httpx, "get", up.http)
    monkeypatch.setattr(httpx, "post", up.http)
    monkeypatch.setattr(overpass, "OVERPASS_MIRRORS", [MIRROR_A, MIRROR_B, MIRROR_C])
    for module in (nominatim, overpass):  # same retries, without the backoff waits
        monkeypatch.setattr(module, "retry", partial(retry, sleep=lambda s: None))
    yield up
    up.release.set()


@pytest.fixture
def upstreams(http, monkeypatch, trained_ranker, rag_index):
    """
    Everything the pipeline needs from outside, faked and healthy: the HTTP
    services above, the LLM client, a ranker model and a retrieval index.
    Tests break one by replacing its handler.
    """
    from backend import config
    from backend.ml.rag import retriever
    from backend.ml.ranker import scorer
    from backend.services import llm

    http.real_get_client = llm.get_client
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "ORS_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_MODELS", ["model-a", "model-b"])
    monkeypatch.setattr(llm, "get_client", lambda: http.llm)
    monkeypatch.setattr(scorer, "_ranker", trained_ranker)
    monkeypatch.setattr(retriever, "_index", rag_index[0])
    monkeypatch.setattr(retriever, "_metadata", rag_index[1])
    return http
