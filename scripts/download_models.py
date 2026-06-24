"""Pre-download all optional ML models used by OMERO.

Run once after installing dependencies:
    python scripts/download_models.py

This primes the HuggingFace cache (~420 MB for dslim/bert-base-NER + ~90 MB
for all-MiniLM-L6-v2) so there is no network fetch on first ingestion.
Whisper model download is handled by the Whisper CLI itself on first use.
"""
from __future__ import annotations

import os
import sys

_API_SRC = os.path.join(os.path.dirname(__file__), "..", "services", "api", "src")
sys.path.insert(0, os.path.abspath(_API_SRC))


def download_sentence_transformers() -> None:
    model_name = os.environ.get(
        "SENTENCE_TRANSFORMERS_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    try:
        from sentence_transformers import SentenceTransformer
        print(f"Downloading sentence-transformers model: {model_name}")
        SentenceTransformer(model_name)
        print(f"  ✓ {model_name} cached")
    except ImportError:
        print("  ⚠ sentence-transformers not installed — skipping")
    except Exception as exc:
        print(f"  ✗ Failed: {exc}")


def download_ner_model() -> None:
    model_name = (
        os.environ.get("ENTITY_NER_MODEL_PATH")
        or os.environ.get("QLORA_ENTITY_MODEL_PATH")
        or "dslim/bert-base-NER"
    )
    if not model_name:
        print("  ⚠ ENTITY_NER_MODEL_PATH not set — skipping NER download")
        return
    try:
        from transformers import pipeline
        print(f"Downloading NER model: {model_name}")
        pipeline("ner", model=model_name, aggregation_strategy="simple")
        print(f"  ✓ {model_name} cached")
    except ImportError:
        print("  ⚠ transformers not installed — skipping")
    except Exception as exc:
        print(f"  ✗ Failed: {exc}")


def main() -> int:
    print("=== OMERO Model Pre-download ===\n")
    download_sentence_transformers()
    download_ner_model()
    print("\n✓ Download complete. Models are cached locally.")
    print("  Whisper models download automatically on first audio upload.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
