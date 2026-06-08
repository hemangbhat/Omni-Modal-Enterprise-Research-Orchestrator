from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


SourceKind = Literal["pdf", "audio"]
JobStatus = Literal["uploaded", "processing", "ready", "failed"]


class IngestionErrorCode(str, Enum):
    UNSUPPORTED_SOURCE = "unsupported_source"
    EXTRACTION_FAILED = "extraction_failed"
    TRANSCRIPTION_FAILED = "transcription_failed"
    EMPTY_TEXT = "empty_text"


@dataclass(frozen=True)
class SourceReference:
    source_path: str
    source_kind: SourceKind
    page_number: int | None = None
    segment_index: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True)
class ExtractedTextSegment:
    text: str
    reference: SourceReference
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class StructuredChunk:
    chunk_index: int
    content: str
    content_hash: str
    source: SourceReference
    start_word: int
    end_word: int
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestionRequest:
    tenant_id: str
    document_id: str
    owner_id: str
    file_path: Path
    source_kind: SourceKind | None = None
    title: str | None = None


@dataclass(frozen=True)
class IngestionResult:
    tenant_id: str
    document_id: str
    owner_id: str
    title: str
    source_kind: SourceKind | None
    status: JobStatus
    chunks: list[StructuredChunk]
    metadata: dict[str, str | int | float | bool]
    error_code: IngestionErrorCode | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class IngestionJob:
    id: str
    request: IngestionRequest
    status: JobStatus
    result: IngestionResult | None = None
    error_code: IngestionErrorCode | None = None
    error_message: str | None = None
