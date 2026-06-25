FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY services/api/pyproject.toml ./
COPY services/api/src/ ./src/

RUN pip install --no-cache-dir -e ".[db,observability,performance,api,saas]"

# Install sentence-transformers and pre-download bge-small-en-v1.5
RUN pip install --no-cache-dir sentence-transformers
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

EXPOSE 8000

RUN useradd -m -u 1000 omero
USER omero

ENV HOST=0.0.0.0
ENV PORT=8000
ENV EMBEDDING_BACKEND=sentence-transformers
ENV SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-small-en-v1.5
ENV EMBEDDING_DIMENSIONS=384

CMD ["python", "-m", "omni_modal.api"]
