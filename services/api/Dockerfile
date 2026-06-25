FROM python:3.11-slim

WORKDIR /app

# System deps: ffmpeg for Whisper audio transcription
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python package with all production extras
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[db,observability,performance,api,saas]"

# Install sentence-transformers for real semantic embeddings (bge-small-en-v1.5).
# Adds ~300MB to image. Remove this line to fall back to hashing embeddings.
RUN pip install --no-cache-dir sentence-transformers

# Pre-download the bge-small-en-v1.5 model so startup is fast in production.
# Remove the two lines below if you prefer to lazy-load on first request.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('BAAI/bge-small-en-v1.5')"

EXPOSE 8000

# Run as non-root for security
RUN useradd -m -u 1000 omero
USER omero

ENV HOST=0.0.0.0
ENV PORT=8000
ENV EMBEDDING_BACKEND=sentence-transformers
ENV SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-small-en-v1.5
ENV EMBEDDING_DIMENSIONS=384

# Use the FastAPI app (async, Pydantic validation, OpenAPI at /docs)
CMD ["python", "-m", "omni_modal.api"]
