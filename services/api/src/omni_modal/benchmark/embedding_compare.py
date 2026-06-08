"""Embedding-quality micro-benchmark.

Computes REAL retrieval metrics (recall@1 and MRR) for a given embedding
provider over a tiny, hand-labeled corpus where each query has exactly one
known-relevant document. Nothing here is hard-coded or invented — every number
is produced by actually embedding the corpus + queries and ranking by cosine
similarity through the same ``InMemoryChunkPersistence`` / ``InMemoryChunkRetriever``
used in production.

Run:
    python -m omni_modal.benchmark.embedding_compare

By default it evaluates the hashing fallback. If ``EMBEDDING_BACKEND`` selects a
real backend (and it is available), that backend is evaluated and printed
alongside hashing so you can compare measured quality.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from omni_modal.ingestion.models import (
    IngestionResult,
    SourceReference,
    StructuredChunk,
)
from omni_modal.qa.embedding_factory import select_embedding_provider
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
from omni_modal.qa.in_memory_store import (
    InMemoryChunkPersistence,
    InMemoryChunkRetriever,
    InMemoryVectorStore,
)
from omni_modal.qa.models import QueryRequest

TENANT = "bench-tenant"

# Each document is a short passage. Each query is a PARAPHRASE of its target
# document that deliberately shares few or no surface tokens with it — this is
# precisely where bag-of-words hashing struggles and real semantics help.
CORPUS: list[tuple[str, str]] = [
    ("doc-cycles", "Enterprise procurement timelines stretched as buyers added compliance review gates before signing contracts."),
    ("doc-retention", "Net revenue retention remained strong at 118 percent, indicating customers expanded their spend over time."),
    ("doc-security", "We prioritized the access-control and audit-logging roadmap to unblock regulated enterprise customers."),
    ("doc-onboarding", "Self-serve onboarding was paused while the team invested in tenant isolation guarantees."),
    ("doc-latency", "Median query response dropped below one second after introducing connection pooling and result caching."),
]

QUERIES: list[tuple[str, str]] = [
    ("Why are deals taking longer to close?", "doc-cycles"),
    ("Are existing customers growing their accounts?", "doc-retention"),
    ("What did we build for compliance-heavy clients?", "doc-security"),
    ("Why did we stop the free signup flow?", "doc-onboarding"),
    ("How did we make search faster?", "doc-latency"),
]


@dataclass(frozen=True)
class EvalResult:
    backend: str
    recall_at_1: float
    mrr: float
    num_queries: int


def evaluate(provider, *, backend_label: str) -> EvalResult:
    """Index CORPUS with ``provider`` and score QUERIES. Returns real metrics."""
    store = InMemoryVectorStore()
    persistence = InMemoryChunkPersistence(store, provider)
    retriever = InMemoryChunkRetriever(provider, store)

    ref = SourceReference(source_path="bench://corpus", source_kind="pdf")
    for index, (doc_id, text) in enumerate(CORPUS):
        persistence.persist(
            IngestionResult(
                tenant_id=TENANT,
                document_id=doc_id,
                owner_id="bench",
                title=doc_id,
                source_kind="pdf",
                status="ready",
                chunks=[
                    StructuredChunk(
                        chunk_index=0,
                        content=text,
                        content_hash=f"{doc_id}-0",
                        source=ref,
                        start_word=0,
                        end_word=len(text.split()),
                    )
                ],
                metadata={},
            )
        )

    hits_at_1 = 0
    reciprocal_ranks = 0.0
    for question, expected_doc in QUERIES:
        results = retriever.retrieve(
            QueryRequest(tenant_id=TENANT, user_id="bench", question=question, top_k=len(CORPUS))
        )
        ranked_ids = [chunk.document_id for chunk in results]
        if ranked_ids and ranked_ids[0] == expected_doc:
            hits_at_1 += 1
        if expected_doc in ranked_ids:
            reciprocal_ranks += 1.0 / (ranked_ids.index(expected_doc) + 1)

    n = len(QUERIES)
    return EvalResult(
        backend=backend_label,
        recall_at_1=hits_at_1 / n,
        mrr=reciprocal_ranks / n,
        num_queries=n,
    )


def main() -> int:
    results: list[EvalResult] = []

    # Always evaluate the deterministic fallback.
    results.append(
        evaluate(HashingQueryEmbeddingProvider(), backend_label="hashing (fallback)")
    )

    # Evaluate the configured backend if it is a real one and available.
    selection = select_embedding_provider()
    if selection.is_semantic and not selection.fell_back:
        results.append(evaluate(selection.provider, backend_label=selection.backend))
    else:
        note = selection.reason or "EMBEDDING_BACKEND not set to a real backend"
        print(f"[info] No real embedding backend evaluated: {note}")

    print(
        json.dumps(
            {
                "dataset": {"documents": len(CORPUS), "queries": len(QUERIES)},
                "results": [
                    {
                        "backend": r.backend,
                        "recall_at_1": round(r.recall_at_1, 3),
                        "mrr": round(r.mrr, 3),
                    }
                    for r in results
                ],
                "note": "Metrics are computed live from real retrieval runs.",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
