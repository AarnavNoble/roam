from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.api.routes import router

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm models so the first request isn't slow
    try:
        from backend.ml.ranker.scorer import get_ranker
        get_ranker()
        print("Ranker loaded.")
    except Exception as e:
        print(f"Ranker pre-warm skipped: {e}")
    try:
        from backend.ml.rag.retriever import _load
        _load()
        print("FAISS index loaded.")
    except Exception as e:
        print(f"FAISS pre-warm skipped: {e}")
    yield


app = FastAPI(title="roam API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
