from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from omni_modal.ingestion.chunking import ChunkingConfig, DeterministicChunker
from omni_modal.ingestion.extractors import (
    AudioTranscriber,
    ExtractionError,
    LocalPdfTextExtractor,
    LocalWhisperTranscriber,
    PdfExtractor,
)
from omni_modal.ingestion.models import (
    ExtractedTextSegment,
    IngestionErrorCode,
    IngestionRequest,
    IngestionResult,
    SourceKind,
)
from omni_modal.ingestion.normalization import normalize_text
from omni_modal.observability import observability

PDF_SUFFIXES = {".pdf"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".webm"}


class ChunkPersistence(Protocol):
    """Persists a ready ``IngestionResult`` (chunks + embeddings) to a store.

    Implementations: ``InMemoryChunkPersistence`` (local demo) or a
    pgvector-backed persistence using ``BatchEmbedder``.
    """

    def persist(self, result: IngestionResult) -> int:
        raise NotImplementedError


def infer_source_kind(file_path: Path) -> SourceKind | None:
    suffix = file_path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return None


class MultimodalIngestionPipeline:
    def __init__(
        self,
        pdf_extractor: PdfExtractor | None = None,
        audio_transcriber: AudioTranscriber | None = None,
        chunker: DeterministicChunker | None = None,
        persistence: "ChunkPersistence | None" = None,
    ) -> None:
        self._pdf_extractor = pdf_extractor or LocalPdfTextExtractor()
        self._audio_transcriber = audio_transcriber or LocalWhisperTranscriber()
        self._chunker = chunker or DeterministicChunker(ChunkingConfig())
        self._persistence = persistence

    def ingest(self, request: IngestionRequest) -> IngestionResult:
        source_kind = request.source_kind or infer_source_kind(request.file_path)
        title = request.title or request.file_path.name
        base_metadata = {
            "source_path": str(request.file_path),
            "file_name": request.file_path.name,
        }

        # Upload safety check (size + MIME type) — Task 11.5
        try:
            from omni_modal.security.upload_safety import assert_upload_safe, UploadSafetyError  # noqa: PLC0415
            try:
                file_size_bytes, detected_mime = assert_upload_safe(request.file_path)
                observability.add_breadcrumb(
                    message="Upload safety check passed",
                    category="ingestion",
                    level="info",
                    data={
                        "document_id": request.document_id,
                        "file_size_bytes": file_size_bytes,
                        "detected_mime_type": detected_mime,
                    },
                )
            except UploadSafetyError as exc:
                observability.add_breadcrumb(
                    message="Upload safety check failed",
                    category="ingestion",
                    level="warning",
                    data={
                        "document_id": request.document_id,
                        "file_size_bytes": exc.file_size,
                        "detected_mime_type": exc.detected_mime,
                        "rejection_reason": str(exc),
                    },
                )
                return self._failed(
                    request, title, source_kind,
                    IngestionErrorCode.UNSUPPORTED_SOURCE,
                    str(exc), base_metadata,
                )
        except ImportError:
            pass  # upload_safety module not available; skip check

        if source_kind is None:
            observability.add_breadcrumb(
                message="Ingestion validation failed: unsupported source type",
                category="ingestion",
                level="warning",
                data={
                    "rejection_reason": "unsupported_source",
                    "document_id": request.document_id,
                    "tenant_id": request.tenant_id,
                    "file_name": request.file_path.name,
                    "file_size_bytes": request.file_path.stat().st_size if request.file_path.exists() else 0,
                    "source_type": str(request.file_path.suffix),
                },
            )
            return self._failed(
                request,
                title,
                None,
                IngestionErrorCode.UNSUPPORTED_SOURCE,
                f"Unsupported source type for {request.file_path.suffix or 'unknown'} file.",
                base_metadata,
            )

        try:
            # Stage: extraction
            observability.add_breadcrumb(
                message="Ingestion stage started: extraction",
                category="ingestion",
                level="info",
                data={"stage": "extraction", "document_id": request.document_id},
            )
            extracted_segments = self._extract(request.file_path, source_kind)

            # Stage: normalization
            observability.add_breadcrumb(
                message="Ingestion stage started: normalization",
                category="ingestion",
                level="info",
                data={"stage": "normalization", "document_id": request.document_id},
            )
            normalized_segments = [
                ExtractedTextSegment(
                    text=normalize_text(segment.text),
                    reference=segment.reference,
                    metadata=segment.metadata,
                )
                for segment in extracted_segments
                if normalize_text(segment.text)
            ]
            if not normalized_segments:
                observability.add_breadcrumb(
                    message="Ingestion validation failed: empty text after normalization",
                    category="ingestion",
                    level="warning",
                    data={
                        "rejection_reason": "empty_text",
                        "document_id": request.document_id,
                        "tenant_id": request.tenant_id,
                        "file_name": request.file_path.name,
                        "source_type": str(source_kind),
                    },
                )
                return self._failed(
                    request,
                    title,
                    source_kind,
                    IngestionErrorCode.EMPTY_TEXT,
                    "No text was extracted from the uploaded file.",
                    base_metadata,
                )

            # Stage: chunking
            observability.add_breadcrumb(
                message="Ingestion stage started: chunking",
                category="ingestion",
                level="info",
                data={"stage": "chunking", "document_id": request.document_id},
            )
            chunks: list = []
            try:
                chunks = self._chunker.chunk(
                    request.tenant_id, request.document_id, normalized_segments
                )
            except Exception as exc:
                observability.capture_exception(
                    exc,
                    operation="ingestion.chunking",
                    context={
                        "document_id": request.document_id,
                        "tenant_id": request.tenant_id,
                        "source_type": source_kind or "unknown",
                        "chunk_index": 0,  # chunking failed before any chunk was produced
                    },
                )
                return self._failed(
                    request,
                    title,
                    source_kind,
                    IngestionErrorCode.EXTRACTION_FAILED,
                    f"Chunking failed: {exc}",
                    base_metadata,
                )

            # Stage: embedding + persistence (only when a persistence backend
            # is injected; otherwise this is a no-op so existing extract-only
            # callers keep working unchanged).
            result = IngestionResult(
                tenant_id=request.tenant_id,
                document_id=request.document_id,
                owner_id=request.owner_id,
                title=title,
                source_kind=source_kind,
                status="ready",
                chunks=chunks,
                metadata={
                    **base_metadata,
                    "segment_count": len(normalized_segments),
                    "chunk_count": len(chunks),
                },
            )

            if self._persistence is not None and chunks:
                observability.add_breadcrumb(
                    message="Ingestion stage started: embedding",
                    category="ingestion",
                    level="info",
                    data={"stage": "embedding", "document_id": request.document_id},
                )
                try:
                    persisted = self._persistence.persist(result)
                    observability.add_breadcrumb(
                        message="Ingestion stage completed: embedding",
                        category="ingestion",
                        level="info",
                        data={
                            "stage": "embedding",
                            "document_id": request.document_id,
                            "persisted_chunks": persisted,
                        },
                    )
                except Exception as exc:
                    observability.capture_exception(
                        exc,
                        operation="ingestion.persistence",
                        context={
                            "document_id": request.document_id,
                            "tenant_id": request.tenant_id,
                            "source_kind": source_kind or "unknown",
                            "chunk_count": len(chunks),
                        },
                    )
                    return self._failed(
                        request,
                        title,
                        source_kind,
                        IngestionErrorCode.EXTRACTION_FAILED,
                        f"Persistence failed: {exc}",
                        base_metadata,
                    )

            return result
        except ExtractionError as exc:
            observability.capture_exception(
                exc,
                operation="ingestion.extract",
                context={
                    "tenant_id": request.tenant_id,
                    "document_id": request.document_id,
                    "source_kind": source_kind or "unknown",
                    "error_code": exc.code.value,
                    "file_name": request.file_path.name,
                    "file_size_bytes": request.file_path.stat().st_size if request.file_path.exists() else 0,
                },
            )
            return self._failed(
                request, title, source_kind, exc.code, str(exc), base_metadata
            )
        except Exception as exc:
            observability.capture_exception(
                exc,
                operation="ingestion.unexpected",
                context={
                    "tenant_id": request.tenant_id,
                    "document_id": request.document_id,
                    "source_kind": source_kind or "unknown",
                },
            )
            return self._failed(
                request,
                title,
                source_kind,
                IngestionErrorCode.EXTRACTION_FAILED,
                f"Unexpected ingestion failure: {exc}",
                base_metadata,
            )

    def _extract(
        self, file_path: Path, source_kind: SourceKind
    ) -> list[ExtractedTextSegment]:
        if source_kind == "pdf":
            return self._pdf_extractor.extract(file_path)
        return self._audio_transcriber.transcribe(file_path)

    @staticmethod
    def _failed(
        request: IngestionRequest,
        title: str,
        source_kind: SourceKind | None,
        error_code: IngestionErrorCode,
        error_message: str,
        metadata: dict[str, str | int | float | bool],
    ) -> IngestionResult:
        return IngestionResult(
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            owner_id=request.owner_id,
            title=title,
            source_kind=source_kind,
            status="failed",
            chunks=[],
            metadata=metadata,
            error_code=error_code,
            error_message=error_message,
        )


def serialize_ingestion_result(result: IngestionResult) -> dict[str, object]:
    payload = asdict(result)
    if result.error_code is not None:
        payload["error_code"] = result.error_code.value
    return payload


def deserialize_ingestion_result(payload: dict[str, object]) -> IngestionResult:
    """Reconstruct an :class:`IngestionResult` from :func:`serialize_ingestion_result`.

    Used by the Redis-backed durable queue so a job processed by a separate
    worker process can be returned to the web tier with a fully-typed result.
    """
    from omni_modal.ingestion.models import (  # noqa: PLC0415
        SourceReference,
        StructuredChunk,
    )

    def _ref(data: dict[str, object]) -> SourceReference:
        return SourceReference(
            source_path=str(data.get("source_path", "")),
            source_kind=data.get("source_kind"),  # type: ignore[arg-type]
            page_number=data.get("page_number"),  # type: ignore[arg-type]
            segment_index=data.get("segment_index"),  # type: ignore[arg-type]
            start_ms=data.get("start_ms"),  # type: ignore[arg-type]
            end_ms=data.get("end_ms"),  # type: ignore[arg-type]
        )

    chunks = [
        StructuredChunk(
            chunk_index=int(c["chunk_index"]),
            content=str(c["content"]),
            content_hash=str(c["content_hash"]),
            source=_ref(dict(c.get("source") or {})),
            start_word=int(c.get("start_word", 0)),
            end_word=int(c.get("end_word", 0)),
            metadata=dict(c.get("metadata") or {}),
        )
        for c in (payload.get("chunks") or [])  # type: ignore[union-attr]
    ]

    error_code_raw = payload.get("error_code")
    error_code = IngestionErrorCode(error_code_raw) if error_code_raw else None

    return IngestionResult(
        tenant_id=str(payload["tenant_id"]),
        document_id=str(payload["document_id"]),
        owner_id=str(payload["owner_id"]),
        title=str(payload["title"]),
        source_kind=payload.get("source_kind"),  # type: ignore[arg-type]
        status=payload.get("status"),  # type: ignore[arg-type]
        chunks=chunks,
        metadata=dict(payload.get("metadata") or {}),
        error_code=error_code,
        error_message=payload.get("error_message"),  # type: ignore[arg-type]
    )
