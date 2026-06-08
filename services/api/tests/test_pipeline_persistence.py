import unittest
from pathlib import Path

import _path  # noqa: F401
from omni_modal.ingestion.batch_embedder import BatchEmbedder
from omni_modal.ingestion.models import (
    ExtractedTextSegment,
    IngestionRequest,
    IngestionResult,
    SourceReference,
)
from omni_modal.ingestion.pipeline import MultimodalIngestionPipeline


class FakePdfExtractor:
    def extract(self, file_path: Path) -> list[ExtractedTextSegment]:
        return [
            ExtractedTextSegment(
                text="Alpha beta gamma delta epsilon zeta eta theta iota kappa.",
                reference=SourceReference(
                    source_path=str(file_path), source_kind="pdf", page_number=1
                ),
                metadata={"page_number": 1},
            )
        ]


class RecordingPersistence:
    def __init__(self) -> None:
        self.calls: list[IngestionResult] = []

    def persist(self, result: IngestionResult) -> int:
        self.calls.append(result)
        return len(result.chunks)


class FailingPersistence:
    def persist(self, result: IngestionResult) -> int:
        raise RuntimeError("db unavailable")


def _request(tmp: Path) -> IngestionRequest:
    target = tmp / "doc.pdf"
    target.write_bytes(b"%PDF-1.4 fake")
    return IngestionRequest(
        tenant_id="t1",
        document_id="11111111-1111-4111-8111-111111111111",
        owner_id="owner",
        file_path=target,
        source_kind="pdf",
        title="Test",
    )


class PipelinePersistenceTests(unittest.TestCase):
    def test_persistence_called_on_success(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            persistence = RecordingPersistence()
            pipeline = MultimodalIngestionPipeline(
                pdf_extractor=FakePdfExtractor(),
                persistence=persistence,
            )
            result = pipeline.ingest(_request(Path(d)))

        self.assertEqual(result.status, "ready")
        self.assertEqual(len(persistence.calls), 1)
        self.assertEqual(persistence.calls[0].document_id, result.document_id)

    def test_no_persistence_is_backward_compatible(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            pipeline = MultimodalIngestionPipeline(pdf_extractor=FakePdfExtractor())
            result = pipeline.ingest(_request(Path(d)))
        self.assertEqual(result.status, "ready")
        self.assertTrue(result.chunks)

    def test_persistence_failure_marks_job_failed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            pipeline = MultimodalIngestionPipeline(
                pdf_extractor=FakePdfExtractor(),
                persistence=FailingPersistence(),
            )
            result = pipeline.ingest(_request(Path(d)))
        self.assertEqual(result.status, "failed")
        self.assertIn("Persistence failed", result.error_message or "")


class BatchEmbedderVectorLiteralTests(unittest.TestCase):
    def test_vector_literal_is_pgvector_format(self) -> None:
        embedder = BatchEmbedder(pool=None)
        literal = embedder._vector_literal([0.1, -0.2, 0.3])
        self.assertTrue(literal.startswith("["))
        self.assertTrue(literal.endswith("]"))
        self.assertNotIn(" ", literal)  # no python-list spaces
        self.assertEqual(literal.count(","), 2)

    def test_vector_literal_round_trips_floats(self) -> None:
        embedder = BatchEmbedder(pool=None)
        literal = embedder._vector_literal([1.0, 2.5])
        inner = literal[1:-1].split(",")
        self.assertEqual([float(x) for x in inner], [1.0, 2.5])


if __name__ == "__main__":
    unittest.main()
