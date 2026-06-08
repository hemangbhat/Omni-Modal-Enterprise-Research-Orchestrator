from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from omni_modal.ingestion.models import ExtractedTextSegment, StructuredChunk

_TOKEN_PATTERN = re.compile(r"\S+")


@dataclass(frozen=True)
class ChunkingConfig:
    max_words: int = 240
    overlap_words: int = 40

    def __post_init__(self) -> None:
        if self.max_words < 1:
            raise ValueError("max_words must be greater than zero.")
        if self.overlap_words < 0:
            raise ValueError("overlap_words cannot be negative.")
        if self.overlap_words >= self.max_words:
            raise ValueError("overlap_words must be smaller than max_words.")


def stable_content_hash(
    tenant_id: str, document_id: str, chunk_index: int, content: str
) -> str:
    payload = f"{tenant_id}:{document_id}:{chunk_index}:{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class DeterministicChunker:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or ChunkingConfig()

    def chunk(
        self,
        tenant_id: str,
        document_id: str,
        segments: list[ExtractedTextSegment],
    ) -> list[StructuredChunk]:
        chunks: list[StructuredChunk] = []
        chunk_index = 0

        for segment in segments:
            words = [match.group(0) for match in _TOKEN_PATTERN.finditer(segment.text)]
            if not words:
                continue

            start = 0
            step = self._config.max_words - self._config.overlap_words
            while start < len(words):
                end = min(start + self._config.max_words, len(words))
                content = " ".join(words[start:end])
                chunks.append(
                    StructuredChunk(
                        chunk_index=chunk_index,
                        content=content,
                        content_hash=stable_content_hash(
                            tenant_id, document_id, chunk_index, content
                        ),
                        source=segment.reference,
                        start_word=start,
                        end_word=end,
                        metadata={
                            **segment.metadata,
                            "overlap_words": self._config.overlap_words,
                            "word_count": end - start,
                        },
                    )
                )
                chunk_index += 1
                if end == len(words):
                    break
                start += step

        return chunks
