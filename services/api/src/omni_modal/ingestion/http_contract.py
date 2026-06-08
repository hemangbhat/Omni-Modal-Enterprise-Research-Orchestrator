from __future__ import annotations

from pathlib import Path
from typing import Any

from omni_modal.ingestion.models import IngestionRequest, SourceKind

ALLOWED_SOURCE_KINDS: set[str] = {"pdf", "audio"}


class IngestionContractError(ValueError):
    pass


def ingestion_request_from_payload(payload: dict[str, Any]) -> IngestionRequest:
    tenant_id = _required_string(payload, "tenant_id")
    document_id = _required_string(payload, "document_id")
    owner_id = _required_string(payload, "owner_id")
    file_path = Path(_required_string(payload, "file_path"))
    source_kind = _optional_source_kind(payload.get("source_kind"))
    title = payload.get("title")
    if title is not None and not isinstance(title, str):
        raise IngestionContractError("title must be a string when provided.")

    return IngestionRequest(
        tenant_id=tenant_id,
        document_id=document_id,
        owner_id=owner_id,
        file_path=file_path,
        source_kind=source_kind,
        title=title,
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IngestionContractError(f"{key} is required.")
    return value


def _optional_source_kind(value: object) -> SourceKind | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in ALLOWED_SOURCE_KINDS:
        raise IngestionContractError("source_kind must be 'pdf' or 'audio'.")
    return value
