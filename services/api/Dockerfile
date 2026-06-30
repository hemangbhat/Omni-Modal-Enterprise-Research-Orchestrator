FROM python:3.11-slim

WORKDIR /app

# System deps: ffmpeg for Whisper audio transcription
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python package with all production extras
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[db,observability,api,saas,scale]"

# sentence-transformers (~400MB RAM) is NOT installed here — Render free tier
# is 512MB and the model load OOMs. Use hashing embeddings on free tier.
# Set EMBEDDING_BACKEND=sentence-transformers on a paid instance (512MB+).

EXPOSE 8000

RUN useradd -m -u 1000 omero
USER omero

ENV HOST=0.0.0.0
ENV PORT=8000
ENV EMBEDDING_BACKEND=hashing
ENV EMBEDDING_DIMENSIONS=384

CMD ["python", "-m", "omni_modal.api"]
