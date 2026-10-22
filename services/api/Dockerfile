FROM python:3.11-slim

WORKDIR /app

# System deps: ffmpeg for Whisper audio transcription
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python package with all production extras
COPY pyproject.toml ./
COPY src/ ./src/

# `performance` provides psycopg-pool: without it the account, SaaS, MFA and
# password-reset stores cannot use Postgres and fall back to process memory.
RUN pip install --no-cache-dir -e ".[db,performance,observability,api,saas,scale,embeddings]"

# Bake the Hugging Face embedding model (bge-small, ONNX, 384-dim) into the
# image so the server never downloads it while serving. It loads on first use
# (~300 MB), which fits the 512 MB free tier; sentence-transformers + torch
# does not, so it is not installed here.
ENV FASTEMBED_CACHE_DIR=/app/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models')"

EXPOSE 8000

RUN useradd -m -u 1000 omero && chown -R omero /app/models
USER omero

ENV HOST=0.0.0.0
ENV PORT=8000
ENV EMBEDDING_BACKEND=fastembed
ENV EMBEDDING_DIMENSIONS=384

CMD ["python", "-m", "omni_modal.api"]
