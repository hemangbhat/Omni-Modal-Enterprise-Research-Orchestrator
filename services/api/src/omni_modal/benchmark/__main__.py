"""
Benchmark harness for Phase 11 — Performance and Scalability.

Usage:
    python -m omni_modal.benchmark [--queries 100] [--docs 10] [--output results.json]

Environment:
    DATABASE_URL  — required for live benchmark
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import statistics
import time
import tempfile
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class BenchmarkStats:
    timestamp: str                       # ISO-8601 UTC
    retrieval_p50_ms: float
    retrieval_p95_ms: float
    retrieval_p99_ms: float
    ingestion_docs_per_minute: float


def serialise_stats(stats: BenchmarkStats) -> dict[str, object]:
    """Return a JSON-serialisable dict with the five required fields."""
    return {
        "timestamp":                stats.timestamp,
        "retrieval_p50_ms":         stats.retrieval_p50_ms,
        "retrieval_p95_ms":         stats.retrieval_p95_ms,
        "retrieval_p99_ms":         stats.retrieval_p99_ms,
        "ingestion_docs_per_minute": stats.ingestion_docs_per_minute,
    }


def run_retrieval_benchmark(
    retriever,
    queries: list[str],
    tenant_id: str,
) -> list[float]:
    """Execute queries sequentially and return wall-clock latencies in ms."""
    from omni_modal.qa.models import QueryRequest
    latencies: list[float] = []
    for question in queries:
        request = QueryRequest(
            tenant_id=tenant_id,
            user_id="benchmark-user",
            question=question,
            top_k=10,
            min_similarity=0.0,
            stream=False,
        )
        start = time.perf_counter()
        try:
            retriever.retrieve(request)
        except Exception:
            pass  # benchmark records latency even on error
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
    return latencies


def compute_percentiles(latencies: list[float]) -> tuple[float, float, float]:
    """Return (p50, p95, p99) from a latency list."""
    n = len(latencies)
    if n == 0:
        return 0.0, 0.0, 0.0
    sorted_lat = sorted(latencies)
    p50 = sorted_lat[int(n * 0.50)]
    p95 = sorted_lat[int(n * 0.95)]
    p99 = sorted_lat[min(int(n * 0.99), n - 1)]
    return p50, p95, p99


def run_ingestion_benchmark(
    queue,
    test_files: list[Path],
    tenant_id: str,
) -> float:
    """Ingest test_files sequentially; return documents per minute."""
    from omni_modal.ingestion.models import IngestionRequest
    import uuid

    start = time.perf_counter()
    for file_path in test_files:
        request = IngestionRequest(
            tenant_id=tenant_id,
            document_id=str(uuid.uuid4()),
            owner_id="benchmark-user",
            file_path=file_path,
        )
        try:
            queue.enqueue(request)
        except Exception:
            pass
    elapsed_seconds = time.perf_counter() - start
    if elapsed_seconds <= 0:
        return 0.0
    return (len(test_files) / elapsed_seconds) * 60


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark retrieval latency and ingestion throughput."
    )
    parser.add_argument("--queries", type=int, default=100, help="Number of retrieval queries to run")
    parser.add_argument("--docs", type=int, default=10, help="Number of documents to ingest")
    parser.add_argument("--output", type=str, default="results.json", help="Output JSON file path")
    args = parser.parse_args()

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Set up retriever
    try:
        from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
        from omni_modal.qa.retrieval import PgVectorChunkRetriever
        retriever = PgVectorChunkRetriever(HashingQueryEmbeddingProvider())
    except Exception as exc:
        print(f"[benchmark] Failed to create retriever: {exc}")
        retriever = None

    # Run retrieval benchmark
    queries = [f"benchmark query {i}" for i in range(args.queries)]
    if retriever is not None:
        latencies = run_retrieval_benchmark(retriever, queries, tenant_id="benchmark-tenant")
        p50, p95, p99 = compute_percentiles(latencies)
    else:
        p50, p95, p99 = 0.0, 0.0, 0.0

    # Run ingestion benchmark (using mock files for now)
    try:
        from omni_modal.ingestion.async_queue import AsyncIngestionQueue
        from omni_modal.ingestion.pipeline import MultimodalIngestionPipeline
        ingestion_queue = AsyncIngestionQueue(MultimodalIngestionPipeline())
        # Use temp PDF-like files for the benchmark (Phase 11 scope)
        test_files = [Path(f"/tmp/benchmark_doc_{i}.pdf") for i in range(args.docs)]
        docs_per_min = run_ingestion_benchmark(ingestion_queue, test_files, "benchmark-tenant")
    except Exception as exc:
        print(f"[benchmark] Ingestion benchmark failed: {exc}")
        docs_per_min = 0.0

    stats = BenchmarkStats(
        timestamp=timestamp,
        retrieval_p50_ms=p50,
        retrieval_p95_ms=p95,
        retrieval_p99_ms=p99,
        ingestion_docs_per_minute=docs_per_min,
    )

    output_path = Path(args.output)
    tmp_path = output_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(serialise_stats(stats), indent=2))
        tmp_path.replace(output_path)
        print(f"[benchmark] Results written to {output_path}")
        print(json.dumps(serialise_stats(stats), indent=2))
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        print(f"[benchmark] Failed to write results: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
