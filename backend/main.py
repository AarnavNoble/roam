from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend import config
from backend.api import agent, auth, routes, trips
from backend.core import telemetry  # noqa: F401  (installs the tracer provider)
from backend.db.session import run_migrations

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    if config.JWT_SECRET == "dev-only-secret-change-me":
        print("WARNING: JWT_SECRET is the development default. Set it before exposing this server.")
    # Pre-warm models so the first request isn't slow
    try:
        from backend.ml.ranker.scorer import get_ranker
        get_ranker()
        print("Ranker loaded.")
    except Exception as e:
        print(f"Ranker pre-warm skipped: {e}")
    try:
        from backend.ml.rag.retriever import _load
        from backend.ml.rag.embedder import get_model
        _load()
        get_model()
        print("FAISS index and embedding model loaded.")
    except Exception as e:
        print(f"FAISS pre-warm skipped: {e}")
    yield


app = FastAPI(title="roam API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

for r in (routes.router, auth.router, trips.router, agent.router):
    app.include_router(r, prefix="/api")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/t/{slug}")
async def shared_trip_page(slug: str):
    """Public page for a shared trip. The page fetches /api/shared/{slug} itself."""
    return FileResponse(STATIC_DIR / "trip.html")
