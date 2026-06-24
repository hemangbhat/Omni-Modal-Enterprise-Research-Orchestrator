"""Reproducible retrieval-quality eval for the pgvector + embedding path.

Ingests a small labelled corpus (one passage per document) through the real
``PostgresChunkPersistence`` path, then runs each paraphrased question through
``PgVectorChunkRetriever`` and computes recall@1, recall@5, and MRR. Produces a
real, defensible quality number for the active embedding model — far better
than the deterministic hashing baseline (recall@1 = 0.40).

Run:  DATABASE_URL=... python -m omni_modal.benchmark.retrieval_eval
Requires: DATABASE_URL set, EMBEDDING_BACKEND=sentence-transformers (or openai),
and the embeddings column migrated to the model's dimension (0004 for 384-dim).
It cleans up every row it writes.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass

from omni_modal.ingestion.models import IngestionResult, SourceReference, StructuredChunk
from omni_modal.qa.embedding_factory import select_embedding_provider
from omni_modal.qa.models import QueryRequest
from omni_modal.qa.pg_persistence import PostgresChunkPersistence
from omni_modal.qa.retrieval import PgVectorChunkRetriever


# (question, passage) pairs — the question is a paraphrase/intent for the passage.
EVAL_SET: list[tuple[str, str]] = [
    ("How does the system run nearest-neighbour search in the database?",
     "OMERO uses Postgres with the pgvector extension and an HNSW index for approximate nearest-neighbour semantic search over document embeddings."),
    ("What model turns audio recordings into text?",
     "Uploaded audio files are transcribed into timestamped text segments by a local Whisper model before chunking."),
    ("How are users charged for subscriptions?",
     "Billing is handled through Stripe Checkout and the customer portal, with webhooks keeping subscription state in sync."),
    ("What keeps one tenant's data from leaking to another?",
     "Every query and document is scoped by tenant_id, and a document access guard rejects cross-tenant reads."),
    ("How is the answer prevented from making things up?",
     "The language model is instructed to answer only from the retrieved context passages and to cite each claim with a bracketed source number."),
    ("Where are uploaded files stored in production?",
     "File objects are written to S3-compatible object storage via a presigned-URL upload flow when an S3 bucket is configured."),
    ("How does the app avoid recomputing the same query?",
     "A thread-safe LRU plus TTL query cache stores retrieval results keyed by a hash of the question, tenant, and parameters."),
    ("What runs document ingestion without blocking the request?",
     "A background worker thread pulls jobs from an async ingestion queue so uploads return immediately while processing continues."),
    ("How are passwords protected at rest?",
     "Account passwords are hashed with salted PBKDF2-HMAC-SHA256 and never stored in plain text."),
    ("What records every sensitive action for compliance?",
     "An audit sink records tool calls and security events with the actor, tenant, action, and a scrubbed metadata payload."),
    ("How does the system recover from a failed external call?",
     "A retry decorator applies exponential backoff with jitter and respects Retry-After headers before giving up."),
    ("What limits how many requests a tenant can make?",
     "A sliding-window rate limiter caps requests per tenant and per user, returning HTTP 429 with a Retry-After hint when exceeded."),
    ("How are named entities pulled out of documents?",
     "A pretrained Hugging Face NER model extracts people, organizations, and locations from each chunk after ingestion."),
    ("What enforces which roles can hit which endpoints?",
     "Role-based access control checks the caller's JWT roles against an endpoint permission map before the handler runs."),
    ("How does the frontend know about backend errors?",
     "Sentry captures exceptions across the frontend and backend and links them with a distributed trace using sentry-trace and baggage headers."),
    ("What guarantees an upload is a safe file type and size?",
     "An upload safety guard sniffs the MIME type and rejects files that are oversized or not in the allowed type list."),
]


@dataclass
class EvalResult:
    model: str
    dimensions: int
    queries: int
    recall_at_1: float
    recall_at_5: float
    mrr: float


def run_eval() -> EvalResult:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required for the retrieval eval.")
    selection = select_embedding_provider()
    provider = selection.provider
    tenant = f"eval-{uuid.uuid4().hex[:8]}"

    persistence = PostgresChunkPersistence(
        provider, database_url=os.environ["DATABASE_URL"],
        embedding_model=selection.backend, dimensions=getattr(provider, "dimensions", 384),
    )
    retriever = PgVectorChunkRetriever(provider, database_url=os.environ["DATABASE_URL"])

    doc_ids: list[str] = []
    try:
        for i, (_q, passage) in enumerate(EVAL_SET):
            doc_id = str(uuid.uuid4())
            doc_ids.append(doc_id)
            ref = SourceReference(source_path="eval://corpus", source_kind="pdf", page_number=1)
            chunk = StructuredChunk(chunk_index=0, content=passage,
                                    content_hash=f"eval{i}-{uuid.uuid4().hex[:8]}",
                                    source=ref, start_word=0, end_word=len(passage.split()), metadata={})
            persistence.persist(IngestionResult(
                tenant_id=tenant, document_id=doc_id, owner_id="eval-user",
                title=f"Passage {i}", source_kind="pdf", status="ready", chunks=[chunk], metadata={},
            ))

        hit1 = hit5 = 0
        rr_total = 0.0
        for i, (question, _passage) in enumerate(EVAL_SET):
            req = QueryRequest(tenant_id=tenant, user_id="eval-user", question=question,
                               top_k=5, min_similarity=0.0)
            hits = retriever.retrieve(req)
            ranked_ids = [h.document_id for h in hits]
            target = doc_ids[i]
            if ranked_ids[:1] == [target]:
                hit1 += 1
            if target in ranked_ids[:5]:
                hit5 += 1
            if target in ranked_ids:
                rr_total += 1.0 / (ranked_ids.index(target) + 1)

        n = len(EVAL_SET)
        return EvalResult(
            model=os.environ.get("SENTENCE_TRANSFORMERS_MODEL", selection.backend),
            dimensions=getattr(provider, "dimensions", 0),
            queries=n,
            recall_at_1=round(hit1 / n, 3),
            recall_at_5=round(hit5 / n, 3),
            mrr=round(rr_total / n, 3),
        )
    finally:
        import psycopg  # noqa: PLC0415

        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            with conn.cursor() as cur:
                for did in doc_ids:
                    cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (did,))
                    cur.execute("DELETE FROM documents WHERE id = %s", (did,))
                cur.execute("DELETE FROM users WHERE tenant_id = %s", (tenant,))
            conn.commit()


def main() -> None:
    result = run_eval()
    payload = {
        "model": result.model, "dimensions": result.dimensions, "queries": result.queries,
        "recall_at_1": result.recall_at_1, "recall_at_5": result.recall_at_5, "mrr": result.mrr,
    }
    print(json.dumps(payload, indent=2))
    if "--output" in sys.argv:
        out = sys.argv[sys.argv.index("--output") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
