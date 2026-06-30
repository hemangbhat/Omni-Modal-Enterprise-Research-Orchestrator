FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY services/api/pyproject.toml ./
COPY services/api/src/ ./src/

# Install only the essentials — no sentence-transformers on the free 512MB tier.
# Embeddings fall back to the deterministic hashing provider (no RAM spike).
# Groq (GROQ_API_KEY) provides grounded LLM answers from the retrieved chunks.
RUN pip install --no-cache-dir -e ".[db,observability,api,saas,scale]"

EXPOSE 8000

RUN useradd -m -u 1000 omero
USER omero

ENV HOST=0.0.0.0
ENV PORT=8000
ENV EMBEDDING_BACKEND=hashing
ENV EMBEDDING_DIMENSIONS=384

CMD ["python", "-m", "omni_modal.api"]
