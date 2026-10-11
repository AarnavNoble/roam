"""
Runtime configuration, read once from the environment.

Everything that differs between a laptop, CI and the deployed container lives
here so the rest of the code never calls os.getenv directly.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"


def _csv(name: str, default: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


# LLM. Models are tried in order, so a retired or rate-limited model costs one
# failed call rather than the whole request.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODELS = _csv("GROQ_MODELS", "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b")

ORS_API_KEY = os.getenv("ORS_API_KEY", "").strip()

OVERPASS_MIRRORS = _csv(
    "OVERPASS_MIRRORS",
    "https://overpass-api.de/api/interpreter,"
    "https://overpass.private.coffee/api/interpreter,"
    "https://lz4.overpass-api.de/api/interpreter,"
    "https://overpass.kumi.systems/api/interpreter",
)

# SQLite keeps local runs zero-setup. Compose, CI and production set a
# postgresql+psycopg:// URL.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'roam.db'}")

JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me")
JWT_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "720"))

# Total wall-clock budget for one itinerary request, in seconds.
REQUEST_DEADLINE_S = float(os.getenv("REQUEST_DEADLINE_S", "60"))

OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
