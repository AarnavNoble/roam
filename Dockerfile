FROM python:3.12-slim

WORKDIR /app

# System deps for LightGBM + sentence-transformers
RUN apt-get update && apt-get install -y gcc g++ && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full project
COPY . .

# Pre-build RAG index + train ranker during image build
# (no API keys needed for these steps)
RUN python -m backend.ml.rag.build_pipeline && \
    python -m backend.ml.ranker.trainer

# HF Spaces requires port 7860
EXPOSE 7860

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]
