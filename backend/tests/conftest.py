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

    for bucket in (auth.auth_bucket, routes.itinerary_bucket, routes.feedback_bucket):
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
