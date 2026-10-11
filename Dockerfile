# roam API image.
#
# Built for Hugging Face Spaces, which runs containers as uid 1000 and routes
# traffic to port 7860. docker compose uses the same image.
#
#   docker build -t roam .
#   docker run -p 7860:7860 -e GROQ_API_KEY=... roam
#
# Configuration is by environment variable, see backend/.env.example. With
# nothing set the API still starts: SQLite under /app/data, and itinerary text
# written without an LLM.

FROM python:3.12-slim

# libgomp1 is the OpenMP runtime LightGBM's wheel links against. Every
# dependency ships a wheel, so no compiler is installed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 roam

ENV HOME=/home/roam \
    HF_HOME=/home/roam/.cache/huggingface \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOKENIZERS_PARALLELISM=false \
    PORT=7860

WORKDIR /app

# Dependencies first, so editing code does not reinstall them. torch comes from
# the CPU index: the default wheel bundles CUDA and is several GB larger.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r backend/requirements.txt

# Only what the API needs (see .dockerignore for what is left out, .env files
# among them). The code stays owned by root and read-only to the app user.
# data/ is the one directory the app writes to: the SQLite database when
# DATABASE_URL is unset, the FAISS index, and ranker models written by
# retraining.
COPY alembic.ini startup.sh ./
COPY backend ./backend
RUN mkdir -p data && chown roam:roam data

USER roam

# Build-time artifacts. Neither needs an API key.
#
# 1. The ranker is trained from fixtures committed to the repo. If this fails
#    it is a bug in our code, so the build fails. It also downloads the
#    embedding model into the image, so the container does not at startup.
RUN python -m backend.ml.ranker.trainer

# 2. The retrieval index is built from pages scraped from Wikivoyage and
#    Reddit, which can be down or rate limiting when the image is built. That
#    must not block a deploy, and it must not go unnoticed either. So a failed
#    or empty index build does NOT fail the build: the partial index is
#    removed, a warning is printed here, and the same warning is printed at
#    every container start by startup.sh, which then retries the build in the
#    background. Until an index exists the API serves itineraries without
#    retrieved context and marks each one "rag:unavailable".
RUN python -m backend.ml.rag.build_pipeline \
    && python -c "import faiss, sys; n = faiss.read_index('data/faiss.index').ntotal; print(f'RAG index built: {n} vectors'); sys.exit(0 if n else 1)" \
    || { rm -f data/faiss.index data/chunks_meta.pkl; \
         echo ''; \
         echo '########################################################################'; \
         echo '# WARNING: the RAG index was NOT built (scrape failed or returned nothing).'; \
         echo '# This image will retry at startup and serve rag:unavailable until then.'; \
         echo '########################################################################'; \
         echo ''; }

EXPOSE 7860

# /api/health answers 200 while the process can serve, including when a
# dependency is down (it reports that in the body). So this restarts a hung
# server, not one that is merely degraded.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '7860') + '/api/health', timeout=4)" || exit 1

CMD ["bash", "startup.sh"]
