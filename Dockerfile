FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY services/api/pyproject.toml ./
COPY services/api/src/ ./src/

# No sentence-transformers/torch on the free 512MB tier. Embeddings come from
# Hugging Face bge-small in ONNX form (fastembed, ~300 MB, loaded on first use).
# Groq (GROQ_API_KEY) provides grounded LLM answers from the retrieved chunks.
# `performance` provides psycopg-pool: without it the account, SaaS, MFA and
# password-reset stores cannot use Postgres and fall back to process memory.
RUN pip install --no-cache-dir -e ".[db,performance,observability,api,saas,scale,embeddings]"

# Bake the model into the image so the server never downloads it while serving.
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
