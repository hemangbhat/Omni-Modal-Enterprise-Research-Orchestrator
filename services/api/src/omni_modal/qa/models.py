from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


QueryStatus = Literal["answered", "no_data", "failed"]


@dataclass(frozen=True)
class SourceReference:
    document_id: str
    chunk_id: str
    title: str
    source_type: str
    chunk_index: int
    page_number: int | None = None
    segment_index: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None

    def citation_label(self, index: int) -> str:
        location = ""
        if self.page_number is not None:
            location = f", p. {self.page_number}"
        elif self.segment_index is not None:
            location = f", segment {self.segment_index}"
        return f"[{index}] {self.title}{location}"


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    title: str
    source_type: str
    chunk_index: int
    content: str
    similarity: float
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)

    def source_reference(self) -> SourceReference:
        return SourceReference(
            document_id=self.document_id,
            chunk_id=self.chunk_id,
            title=self.title,
            source_type=self.source_type,
            chunk_index=self.chunk_index,
            page_number=_optional_int(self.metadata.get("pageNumber"))
            or _optional_int(self.metadata.get("page_number")),
            segment_index=_optional_int(self.metadata.get("segmentIndex"))
            or _optional_int(self.metadata.get("segment_index")),
            start_ms=_optional_int(self.metadata.get("startMs"))
            or _optional_int(self.metadata.get("start_ms")),
            end_ms=_optional_int(self.metadata.get("endMs"))
            or _optional_int(self.metadata.get("end_ms")),
        )


@dataclass(frozen=True)
class QueryRequest:
    tenant_id: str
    user_id: str
    question: str
    top_k: int = 5
    min_similarity: float = 0.0
    stream: bool = False


@dataclass(frozen=True)
class QueryResponse:
    status: QueryStatus
    question: str
    answer_markdown: str
    citations: list[SourceReference]
    retrieved_chunks: list[RetrievedChunk]
    error_message: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "question": self.question,
            "answer_markdown": self.answer_markdown,
            "citations": [asdict(citation) for citation in self.citations],
            "retrieved_chunks": [asdict(chunk) for chunk in self.retrieved_chunks],
            "error_message": self.error_message,
        }


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
