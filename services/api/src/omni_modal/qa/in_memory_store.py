"""In-memory vector store + retriever for local, Postgres-free demos.

This gives the system a genuine end-to-end path without requiring a running
PostgreSQL + pgvector instance:

    ingest (/ingest/local) -> InMemoryChunkPersistence -> InMemoryVectorStore
                                                              |
    query (/query, /query/stream) <- InMemoryChunkRetriever <-

It is NOT a production store (single process, no persistence to disk, linear
scan). It exists so the demo works locally and so the wiring between ingestion
and retrieval is exercised by real code rather than mocks.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

from omni_modal.ingestion.models import IngestionResult
from omni_modal.qa.embeddings import QueryEmbeddingProvider
from omni_modal.qa.models import QueryRequest, RetrievedChunk


@dataclass
class StoredChunk:
    tenant_id: str
    document_id: str
    title: str
    source_type: str
    chunk_id: str
    chunk_index: int
    content: str
    embedding: list[float]
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 if either vector is zero."""
    if len(a) != len(b):
        raise ValueError("Vectors must have the same dimension.")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class InMemoryVectorStore:
    """Thread-safe in-memory store of embedded chunks keyed by tenant."""

    def __init__(self) -> None:
        self._chunks: list[StoredChunk] = []
        self._lock = threading.Lock()

    def add(self, chunk: StoredChunk) -> None:
        with self._lock:
            # Idempotent: replace any existing chunk with the same id.
            self._chunks = [c for c in self._chunks if c.chunk_id != chunk.chunk_id]
            self._chunks.append(chunk)

    def add_many(self, chunks: list[StoredChunk]) -> int:
        for chunk in chunks:
            self.add(chunk)
        return len(chunks)

    def for_tenant(self, tenant_id: str) -> list[StoredChunk]:
        with self._lock:
            return [c for c in self._chunks if c.tenant_id == tenant_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._chunks)


class InMemoryChunkPersistence:
    """Persists an ``IngestionResult``'s chunks into an ``InMemoryVectorStore``.

    Embeddings are generated with the injected embedding provider so the same
    provider can be reused for queries (keeping vectors comparable).
    """

    def __init__(
        self,
        store: InMemoryVectorStore,
        embedding_provider: QueryEmbeddingProvider,
    ) -> None:
        self._store = store
        self._provider = embedding_provider

    def persist(self, result: IngestionResult) -> int:
        if result.status != "ready" or not result.chunks:
            return 0

        contents = [chunk.content for chunk in result.chunks]
        embed_documents = getattr(self._provider, "embed_documents", None)
        if callable(embed_documents):
            embeddings = embed_documents(contents)
        else:
            embeddings = [self._provider.embed_query(text) for text in contents]

        source_type = result.source_kind or "note"
        stored: list[StoredChunk] = []
        for chunk, embedding in zip(result.chunks, embeddings):
            stored.append(
                StoredChunk(
                    tenant_id=result.tenant_id,
                    document_id=result.document_id,
                    title=result.title,
                    source_type=source_type,
                    chunk_id=f"{result.document_id}:{chunk.chunk_index}",
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    embedding=embedding,
                    metadata=dict(chunk.metadata),
                )
            )
        return self._store.add_many(stored)


class InMemoryChunkRetriever:
    """ChunkRetriever backed by an InMemoryVectorStore (cosine similarity)."""

    def __init__(
        self,
        embedding_provider: QueryEmbeddingProvider,
        store: InMemoryVectorStore,
    ) -> None:
        self._provider = embedding_provider
        self._store = store

    def retrieve(self, request: QueryRequest) -> list[RetrievedChunk]:
        query_embedding = self._provider.embed_query(request.question)
        scored: list[tuple[float, StoredChunk]] = []
        for chunk in self._store.for_tenant(request.tenant_id):
            similarity = cosine_similarity(query_embedding, chunk.embedding)
            if request.min_similarity > 0 and similarity < request.min_similarity:
                continue
            scored.append((similarity, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[: max(0, request.top_k)]
        return [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                title=chunk.title,
                source_type=chunk.source_type,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                similarity=float(similarity),
                metadata=dict(chunk.metadata),
            )
            for similarity, chunk in top
        ]
