import unittest
from pathlib import Path

import _path  # noqa: F401
from omni_modal.ingestion import (
    ChunkingConfig,
    DeterministicChunker,
    ExtractedTextSegment,
    ExtractionError,
    InMemoryIngestionQueue,
    IngestionErrorCode,
    IngestionRequest,
    MultimodalIngestionPipeline,
    SourceReference,
    normalize_text,
)


class FakePdfExtractor:
    def extract(self, file_path: Path) -> list[ExtractedTextSegment]:
        return [
            ExtractedTextSegment(
                text="Alpha beta gamma delta epsilon zeta eta theta iota kappa.",
                reference=SourceReference(
                    source_path=str(file_path),
                    source_kind="pdf",
                    page_number=1,
                ),
                metadata={"page_number": 1},
            )
        ]


class FakeAudioTranscriber:
    def transcribe(self, file_path: Path) -> list[ExtractedTextSegment]:
        return [
            ExtractedTextSegment(
                text="Audio segment one. Audio segment two.",
                reference=SourceReference(
                    source_path=str(file_path),
                    source_kind="audio",
                    segment_index=0,
                    start_ms=0,
                    end_ms=1200,
                ),
                metadata={"segment_index": 0},
            )
        ]


class FailingPdfExtractor:
    def extract(self, file_path: Path) -> list[ExtractedTextSegment]:
        raise ExtractionError(
            IngestionErrorCode.EXTRACTION_FAILED,
            "synthetic extraction failure",
        )


class IngestionPipelineTest(unittest.TestCase):
    def test_normalization_removes_pdf_hyphenation_and_extra_space(self) -> None:
        text = "Enter-\nprise   research\r\n\r\n\r\n  pipeline"

        self.assertEqual(normalize_text(text), "Enterprise research\n\npipeline")

    def test_chunking_is_deterministic_with_overlap_and_source_reference(self) -> None:
        chunker = DeterministicChunker(
            ChunkingConfig(max_words=4, overlap_words=1)
        )
        segment = ExtractedTextSegment(
            text="one two three four five six seven",
            reference=SourceReference(
                source_path="sample.pdf",
                source_kind="pdf",
                page_number=3,
            ),
        )

        first = chunker.chunk("tenant", "doc", [segment])
        second = chunker.chunk("tenant", "doc", [segment])

        self.assertEqual(first, second)
        self.assertEqual([chunk.content for chunk in first], [
            "one two three four",
            "four five six seven",
        ])
        self.assertEqual(first[0].source.page_number, 3)
        self.assertEqual(first[1].start_word, 3)

    def test_pdf_ingestion_returns_ready_chunks_with_metadata(self) -> None:
        pipeline = MultimodalIngestionPipeline(
            pdf_extractor=FakePdfExtractor(),
            audio_transcriber=FakeAudioTranscriber(),
            chunker=DeterministicChunker(ChunkingConfig(max_words=5, overlap_words=1)),
        )
        request = IngestionRequest(
            tenant_id="tenant",
            document_id="doc",
            owner_id="user",
            file_path=Path("buyer-interviews.pdf"),
        )

        result = pipeline.ingest(request)

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.source_kind, "pdf")
        self.assertEqual(result.metadata["chunk_count"], 3)
        self.assertEqual(result.chunks[0].source.page_number, 1)
        self.assertEqual(result.chunks[0].metadata["word_count"], 5)

    def test_audio_ingestion_uses_local_transcriber_contract(self) -> None:
        pipeline = MultimodalIngestionPipeline(
            pdf_extractor=FakePdfExtractor(),
            audio_transcriber=FakeAudioTranscriber(),
            chunker=DeterministicChunker(ChunkingConfig(max_words=10, overlap_words=2)),
        )
        request = IngestionRequest(
            tenant_id="tenant",
            document_id="doc",
            owner_id="user",
            file_path=Path("call.wav"),
        )

        result = pipeline.ingest(request)

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.source_kind, "audio")
        self.assertEqual(result.chunks[0].source.start_ms, 0)
        self.assertEqual(result.chunks[0].source.end_ms, 1200)

    def test_pipeline_returns_failed_result_for_extraction_errors(self) -> None:
        pipeline = MultimodalIngestionPipeline(
            pdf_extractor=FailingPdfExtractor(),
            audio_transcriber=FakeAudioTranscriber(),
        )
        request = IngestionRequest(
            tenant_id="tenant",
            document_id="doc",
            owner_id="user",
            file_path=Path("broken.pdf"),
        )

        result = pipeline.ingest(request)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, IngestionErrorCode.EXTRACTION_FAILED)
        self.assertEqual(result.chunks, [])

    def test_queue_stub_tracks_lifecycle(self) -> None:
        pipeline = MultimodalIngestionPipeline(
            pdf_extractor=FakePdfExtractor(),
            audio_transcriber=FakeAudioTranscriber(),
        )
        queue = InMemoryIngestionQueue(pipeline)
        job = queue.enqueue(
            IngestionRequest(
                tenant_id="tenant",
                document_id="doc",
                owner_id="user",
                file_path=Path("buyer-interviews.pdf"),
            )
        )

        self.assertEqual(job.status, "uploaded")
        completed = queue.process(job.id)

        self.assertEqual(completed.status, "ready")
        self.assertIsNotNone(completed.result)


if __name__ == "__main__":
    unittest.main()
