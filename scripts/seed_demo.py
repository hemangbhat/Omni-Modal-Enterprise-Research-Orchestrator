"""Seed a small in-memory corpus and run a sample retrieval.

This is a self-contained, dependency-free smoke test of the genuine
ingestion-persistence -> retrieval wiring. It does NOT require PostgreSQL,
pgvector, a real embedding API, or the HTTP server. It exercises the same
``InMemoryChunkPersistence`` and ``InMemoryChunkRetriever`` the backend uses
on its local (Postgres-free) path.

Run from the repo root:

    python scripts/seed_demo.py

You should see the most relevant seeded chunk ranked first for the sample
query. This confirms retrieval returns real, ranked results.
"""
from __future__ import annotations

import os
import sys

_API_SRC = os.path.join(os.path.dirname(__file__), "..", "services", "api", "src")
sys.path.insert(0, os.path.abspath(_API_SRC))

from omni_modal.ingestion.models import (  # noqa: E402
    IngestionResult,
    SourceReference,
    StructuredChunk,
)
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider  # noqa: E402
from omni_modal.qa.in_memory_store import (  # noqa: E402
    InMemoryChunkPersistence,
    InMemoryChunkRetriever,
    InMemoryVectorStore,
)
from omni_modal.qa.models import QueryRequest  # noqa: E402

TENANT = "demo-tenant"

SAMPLE_DOCS = [
    (
        "doc-earnings",
        "Q3 Earnings Call Transcript",
        "audio",
        [
            "Enterprise procurement cycles lengthened this quarter as customers "
            "added compliance review steps before signing.",
            "Despite longer deal cycles, net revenue retention stayed strong at "
            "118 percent, signaling durable demand.",
        ],
    ),
    (
        "doc-strategy",
        "Product Strategy Memo",
        "pdf",
        [
            "We are prioritizing the security and access-control roadmap to "
            "unblock regulated enterprise buyers.",
            "Self-serve onboarding will be paused while we invest in audit "
            "logging and tenant isolation guarantees.",
        ],
    ),
]


def _make_result(document_id: str, title: str, source_kind: str, paragraphs: list[str]) -> IngestionResult:
    ref = SourceReference(source_path=f"seed://{document_id}", source_kind=source_kind)
    chunks = [
        StructuredChunk(
            chunk_index=i,
            content=text,
            content_hash=f"{document_id}-{i}",
            source=ref,
            start_word=0,
            end_word=len(text.split()),
        )
        for i, text in enumerate(paragraphs)
    ]
    return IngestionResult(
        tenant_id=TENANT,
        document_id=document_id,
        owner_id="seed-owner",
        title=title,
        source_kind=source_kind,
        status="ready",
        chunks=chunks,
        metadata={"chunk_count": len(chunks)},
    )


def main() -> int:
    provider = HashingQueryEmbeddingProvider()
    store = InMemoryVectorStore()
    persistence = InMemoryChunkPersistence(store, provider)
    retriever = InMemoryChunkRetriever(provider, store)

    for document_id, title, source_kind, paragraphs in SAMPLE_DOCS:
        persisted = _make_result(document_id, title, source_kind, paragraphs)
        count = persistence.persist(persisted)
        print(f"Seeded {count} chunks from '{title}'.")

    print(f"\nTotal chunks in store: {len(store)}")

    question = "Why are enterprise deal cycles getting longer?"
    print(f"\nQuery: {question}\n")
    results = retriever.retrieve(
        QueryRequest(tenant_id=TENANT, user_id="seed-user", question=question, top_k=3)
    )
    if not results:
        print("No results — something is wrong with the wiring.")
        return 1

    for rank, chunk in enumerate(results, start=1):
        print(f"[{rank}] sim={chunk.similarity:.3f}  {chunk.title}")
        print(f"     {chunk.content}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
