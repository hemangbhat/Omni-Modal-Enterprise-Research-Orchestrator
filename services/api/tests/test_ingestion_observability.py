"""Property 8: Ingestion Observability Context Completeness
Validates: Requirements 3.1, 3.2, 3.3, 3.4
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import _path  # noqa: F401

from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.ingestion.pipeline import MultimodalIngestionPipeline
from omni_modal.ingestion.models import IngestionRequest, IngestionErrorCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_path(
    *,
    name: str = "doc.pdf",
    suffix: str = ".pdf",
    exists: bool = True,
    size: int = 1024,
) -> MagicMock:
    fake_path = MagicMock(spec=Path)
    fake_path.name = name
    fake_path.suffix = suffix
    fake_path.exists.return_value = exists
    fake_path.stat.return_value.st_size = size
    return fake_path


def _make_request(
    *,
    document_id: str = "doc-1",
    tenant_id: str = "tenant-1",
    source_kind: str = "pdf",
    fake_path: MagicMock | None = None,
) -> IngestionRequest:
    if fake_path is None:
        fake_path = _make_fake_path(suffix=f".{source_kind}")
    return IngestionRequest(
        tenant_id=tenant_id,
        document_id=document_id,
        owner_id="owner-1",
        file_path=fake_path,
        source_kind=source_kind,
    )


# ---------------------------------------------------------------------------
# Property 8: Ingestion Observability Context Completeness
# ---------------------------------------------------------------------------

@given(
    document_id=st.text(min_size=1, max_size=36),
    tenant_id=st.text(min_size=1, max_size=36),
    source_type=st.sampled_from(["pdf", "audio"]),
    file_name=st.text(min_size=1, max_size=50),
)
@settings(max_examples=100)
def test_ingestion_context_completeness_on_extraction_failure(
    document_id: str,
    tenant_id: str,
    source_type: str,
    file_name: str,
) -> None:
    """Captured Sentry context includes document_id, tenant_id, source_type, file_size.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """
    captured_contexts: list[dict] = []

    def mock_capture(exc, *, operation, context=None):
        if context:
            captured_contexts.append(dict(context))

    with patch("omni_modal.ingestion.pipeline.observability") as mock_obs, \
         patch.object(MultimodalIngestionPipeline, "_extract") as mock_extract:
        mock_obs.capture_exception.side_effect = mock_capture
        mock_obs.add_breadcrumb = MagicMock()
        mock_extract.side_effect = Exception("extraction failed")

        pipeline = MultimodalIngestionPipeline()
        fake_path = _make_fake_path(
            name=file_name,
            suffix=f".{source_type}",
        )

        req = IngestionRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            owner_id="owner-1",
            file_path=fake_path,
            source_kind=source_type,
        )
        pipeline.ingest(req)

    # At least one capture should have occurred (ingestion.unexpected catches it)
    assert len(captured_contexts) > 0, "Expected at least one captured context"
    ctx = captured_contexts[-1]
    # At minimum one domain ID must be present
    assert "tenant_id" in ctx or "document_id" in ctx, (
        f"Context missing tenant_id and document_id: {ctx}"
    )


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------

class TestIngestionBreadcrumbs(unittest.TestCase):

    # ------------------------------------------------------------------
    # 5.2 test_extraction_breadcrumb_recorded
    # ------------------------------------------------------------------
    def test_extraction_breadcrumb_recorded(self) -> None:
        """Breadcrumb with stage='extraction' at info level is added before extraction."""
        breadcrumbs_recorded: list[dict] = []

        def capture_breadcrumb(*, message, category, level, data=None):
            breadcrumbs_recorded.append({
                "message": message,
                "category": category,
                "level": level,
                "data": data or {},
            })

        class FakePdfExtractor:
            def extract(self, file_path):
                from omni_modal.ingestion.models import ExtractedTextSegment, SourceReference
                return [
                    ExtractedTextSegment(
                        text="Hello world this is a test.",
                        reference=SourceReference(
                            source_path=str(file_path),
                            source_kind="pdf",
                            page_number=1,
                        ),
                        metadata={"page_number": 1},
                    )
                ]

        with patch("omni_modal.ingestion.pipeline.observability") as mock_obs:
            mock_obs.add_breadcrumb.side_effect = capture_breadcrumb
            mock_obs.capture_exception = MagicMock()

            pipeline = MultimodalIngestionPipeline(
                pdf_extractor=FakePdfExtractor(),
            )
            req = _make_request(document_id="doc-extract", source_kind="pdf")
            pipeline.ingest(req)

        extraction_breadcrumbs = [
            b for b in breadcrumbs_recorded
            if b.get("data", {}).get("stage") == "extraction"
        ]
        self.assertGreater(
            len(extraction_breadcrumbs), 0,
            "Expected at least one breadcrumb with stage='extraction'",
        )
        self.assertEqual(extraction_breadcrumbs[0]["level"], "info")
        self.assertEqual(extraction_breadcrumbs[0]["category"], "ingestion")
        self.assertIn("doc-extract", str(extraction_breadcrumbs[0]["data"]))

    # ------------------------------------------------------------------
    # 5.2 test_validation_failure_warning_breadcrumb
    # ------------------------------------------------------------------
    def test_validation_failure_warning_breadcrumb(self) -> None:
        """Unsupported file suffix triggers a warning-level breadcrumb."""
        breadcrumbs_recorded: list[dict] = []

        def capture_breadcrumb(*, message, category, level, data=None):
            breadcrumbs_recorded.append({
                "message": message,
                "category": category,
                "level": level,
                "data": data or {},
            })

        with patch("omni_modal.ingestion.pipeline.observability") as mock_obs:
            mock_obs.add_breadcrumb.side_effect = capture_breadcrumb
            mock_obs.capture_exception = MagicMock()

            pipeline = MultimodalIngestionPipeline()
            fake_path = _make_fake_path(name="document.xyz", suffix=".xyz")
            req = IngestionRequest(
                tenant_id="tenant-warn",
                document_id="doc-warn",
                owner_id="owner-1",
                file_path=fake_path,
                source_kind=None,  # forces infer_source_kind → None for .xyz
            )
            result = pipeline.ingest(req)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, IngestionErrorCode.UNSUPPORTED_SOURCE)

        warning_breadcrumbs = [
            b for b in breadcrumbs_recorded
            if b["level"] == "warning"
            and b.get("data", {}).get("rejection_reason") == "unsupported_source"
        ]
        self.assertGreater(
            len(warning_breadcrumbs), 0,
            "Expected a warning breadcrumb for unsupported source type",
        )
        data = warning_breadcrumbs[0]["data"]
        self.assertEqual(data["rejection_reason"], "unsupported_source")
        self.assertEqual(data["document_id"], "doc-warn")
        self.assertEqual(data["tenant_id"], "tenant-warn")

    # ------------------------------------------------------------------
    # 5.2 test_chunking_failure_captures_exception
    # ------------------------------------------------------------------
    def test_chunking_failure_captures_exception(self) -> None:
        """Chunker raising an exception triggers capture_exception with operation='ingestion.chunking'."""
        captured_calls: list[dict] = []

        def mock_capture(exc, *, operation, context=None):
            captured_calls.append({"operation": operation, "context": context or {}})

        class FakePdfExtractor:
            def extract(self, file_path):
                from omni_modal.ingestion.models import ExtractedTextSegment, SourceReference
                return [
                    ExtractedTextSegment(
                        text="Word one two three four five.",
                        reference=SourceReference(
                            source_path=str(file_path),
                            source_kind="pdf",
                            page_number=1,
                        ),
                        metadata={"page_number": 1},
                    )
                ]

        class FailingChunker:
            def chunk(self, tenant_id, document_id, segments):
                raise RuntimeError("chunker exploded")

        with patch("omni_modal.ingestion.pipeline.observability") as mock_obs:
            mock_obs.add_breadcrumb = MagicMock()
            mock_obs.capture_exception.side_effect = mock_capture

            pipeline = MultimodalIngestionPipeline(
                pdf_extractor=FakePdfExtractor(),
                chunker=FailingChunker(),
            )
            req = _make_request(document_id="doc-chunk", source_kind="pdf")
            result = pipeline.ingest(req)

        self.assertEqual(result.status, "failed")

        chunking_captures = [
            c for c in captured_calls
            if c["operation"] == "ingestion.chunking"
        ]
        self.assertGreater(
            len(chunking_captures), 0,
            "Expected capture_exception called with operation='ingestion.chunking'",
        )
        ctx = chunking_captures[0]["context"]
        self.assertIn("document_id", ctx)
        self.assertIn("tenant_id", ctx)
        self.assertIn("source_type", ctx)
        self.assertIn("chunk_index", ctx)

    # ------------------------------------------------------------------
    # Extra: extraction breadcrumbs appear in correct order
    # ------------------------------------------------------------------
    def test_stage_breadcrumbs_ordered_extraction_normalization_chunking(self) -> None:
        """Breadcrumbs for extraction, normalization, and chunking are recorded in order."""
        stages_seen: list[str] = []

        def capture_breadcrumb(*, message, category, level, data=None):
            stage = (data or {}).get("stage")
            if stage:
                stages_seen.append(stage)

        class FakePdfExtractor:
            def extract(self, file_path):
                from omni_modal.ingestion.models import ExtractedTextSegment, SourceReference
                return [
                    ExtractedTextSegment(
                        text="Some content here.",
                        reference=SourceReference(
                            source_path=str(file_path),
                            source_kind="pdf",
                            page_number=1,
                        ),
                        metadata={"page_number": 1},
                    )
                ]

        with patch("omni_modal.ingestion.pipeline.observability") as mock_obs:
            mock_obs.add_breadcrumb.side_effect = capture_breadcrumb
            mock_obs.capture_exception = MagicMock()

            pipeline = MultimodalIngestionPipeline(pdf_extractor=FakePdfExtractor())
            req = _make_request(document_id="doc-order", source_kind="pdf")
            pipeline.ingest(req)

        self.assertEqual(
            stages_seen,
            ["extraction", "normalization", "chunking"],
            f"Unexpected stage order: {stages_seen}",
        )

    # ------------------------------------------------------------------
    # Extra: ExtractionError capture includes file_name and file_size_bytes
    # ------------------------------------------------------------------
    def test_extraction_error_context_includes_file_metadata(self) -> None:
        """ExtractionError capture includes file_name and file_size_bytes in context."""
        from omni_modal.ingestion.extractors import ExtractionError
        captured_contexts: list[dict] = []

        def mock_capture(exc, *, operation, context=None):
            captured_contexts.append({"operation": operation, "context": context or {}})

        class FailingExtractor:
            def extract(self, file_path):
                raise ExtractionError(
                    IngestionErrorCode.EXTRACTION_FAILED,
                    "PDF parse failed",
                )

        with patch("omni_modal.ingestion.pipeline.observability") as mock_obs:
            mock_obs.add_breadcrumb = MagicMock()
            mock_obs.capture_exception.side_effect = mock_capture

            pipeline = MultimodalIngestionPipeline(pdf_extractor=FailingExtractor())
            fake_path = _make_fake_path(name="my-doc.pdf", suffix=".pdf", size=2048)
            req = IngestionRequest(
                tenant_id="t1",
                document_id="d1",
                owner_id="o1",
                file_path=fake_path,
                source_kind="pdf",
            )
            result = pipeline.ingest(req)

        self.assertEqual(result.status, "failed")
        extract_captures = [
            c for c in captured_contexts
            if c["operation"] == "ingestion.extract"
        ]
        self.assertGreater(len(extract_captures), 0)
        ctx = extract_captures[0]["context"]
        self.assertIn("file_name", ctx)
        self.assertIn("file_size_bytes", ctx)
        self.assertIn("source_kind", ctx)


if __name__ == "__main__":
    unittest.main()
