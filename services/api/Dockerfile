FROM python:3.11-slim

WORKDIR /app

# Install system deps for pypdf (and ffmpeg for Whisper if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python package
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[db,observability,performance]"

# Optional: install sentence-transformers for real semantic embeddings
# Uncomment to include in the image (~500MB larger):
# RUN pip install --no-cache-dir sentence-transformers

# Optional: install Whisper for audio transcription
# Uncomment to include:
# RUN pip install --no-cache-dir openai-whisper

EXPOSE 8000

# Run as non-root
RUN useradd -m -u 1000 omero
USER omero

ENV HOST=0.0.0.0
ENV PORT=8000

CMD ["python", "-m", "omni_modal.main"]
