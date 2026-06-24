"""Entity extraction service — wired into the ingestion pipeline.

Selected by ``ENTITY_NER_MODEL_PATH`` (preferred) or the legacy
``QLORA_ENTITY_MODEL_PATH`` env var (kept for backward compatibility):
  - Unset or empty   → RuleBasedEnterpriseEntityExtractor (zero deps, always works)
  - A HF model ID or local path (e.g. "dslim/bert-base-NER") → a pretrained
    Hugging Face token-classification (NER) model. NOTE: this loads a
    *pretrained* model; it is NOT a QLoRA fine-tune. A QLoRA training pipeline
    is scaffolded separately but no fine-tuned weights are produced.
    (requires: pip install transformers torch)

The service is safe to call on any chunk; it never raises (logs failures
through observability and returns an empty output on error).

Records are accumulated in ``_records`` (keyed by ``tenant_id:document_id``)
so the GET /entities endpoint can retrieve them without a database.
"""
from __future__ import annotations

import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from omni_modal.entity_extraction.inference import (
    EnterpriseEntityExtractor,
    QLoRAEnterpriseEntityExtractor,
    RuleBasedEnterpriseEntityExtractor,
)
from omni_modal.entity_extraction.schema import ExtractionOutput
from omni_modal.entity_extraction.storage import output_to_entity_records
from omni_modal.ingestion.models import IngestionResult
from omni_modal.observability import observability


def _resolve_ner_model_path() -> str:
    """Return the configured NER model path.

    Prefers the honest ``ENTITY_NER_MODEL_PATH`` and falls back to the legacy
    ``QLORA_ENTITY_MODEL_PATH`` so existing .env files keep working.
    """
    new = os.environ.get("ENTITY_NER_MODEL_PATH", "").strip()
    if new:
        return new
    return os.environ.get("QLORA_ENTITY_MODEL_PATH", "").strip()


def build_entity_extractor() -> EnterpriseEntityExtractor:
    """Construct the best available extractor from environment config."""
    model_path_raw = _resolve_ner_model_path()
    if not model_path_raw:
        return RuleBasedEnterpriseEntityExtractor()
    try:
        extractor = QLoRAEnterpriseEntityExtractor(Path(model_path_raw))
        observability.add_breadcrumb(
            message="Entity extractor loaded",
            category="entity_extraction",
            level="info",
            data={"model": model_path_raw},
        )
        return extractor
    except Exception as exc:
        observability.capture_message(
            f"Failed to load NER model '{model_path_raw}': {exc}. Falling back to rule-based.",
            operation="entity_extraction.build",
            level="warning",
        )
        return RuleBasedEnterpriseEntityExtractor()


class EntityExtractionService:
    """Runs entity extraction on every chunk of a completed IngestionResult.

    Designed to be called by the BackgroundWorker after a successful ingest.
    Entity records are persisted in ``_records`` (in-memory, keyed by
    ``tenant_id:document_id``) so the /entities API can retrieve them.
    """

    def __init__(self, extractor: EnterpriseEntityExtractor | None = None) -> None:
        self._extractor = extractor or build_entity_extractor()
        # In-memory record store: "tenant_id:document_id" → list[EntityRecord]
        self._records: dict[str, list[Any]] = defaultdict(list)
        self._lock = threading.Lock()

    def extract_from_result(self, result: IngestionResult) -> list[ExtractionOutput]:
        if result.status != "ready" or not result.chunks:
            return []

        outputs: list[ExtractionOutput] = []
        for chunk in result.chunks:
            try:
                output = self._extractor.extract(
                    tenant_id=result.tenant_id,
                    document_id=result.document_id,
                    text=chunk.content,
                    chunk_id=chunk.content_hash,
                )
                outputs.append(output)
                observability.add_breadcrumb(
                    message="Entity extraction completed for chunk",
                    category="entity_extraction",
                    level="info",
                    data={
                        "document_id": result.document_id,
                        "chunk_index": chunk.chunk_index,
                        "entity_count": len(output.entities),
                    },
                )
            except Exception as exc:
                observability.capture_exception(
                    exc,
                    operation="entity_extraction.chunk",
                    context={
                        "document_id": result.document_id,
                        "chunk_index": chunk.chunk_index,
                    },
                )
        return outputs

    def entity_records_from_result(self, result: IngestionResult) -> list[object]:
        """Extract entities, store them in-memory, and return as EntityRecord list."""
        records = []
        for output in self.extract_from_result(result):
            new_records = output_to_entity_records(output)
            records.extend(new_records)

        if records:
            key = f"{result.tenant_id}:{result.document_id}"
            with self._lock:
                self._records[key].extend(records)

        return records

    def get_records(self, tenant_id: str, document_id: str) -> list[Any]:
        """Return stored entity records for a specific document."""
        key = f"{tenant_id}:{document_id}"
        with self._lock:
            return list(self._records.get(key, []))
