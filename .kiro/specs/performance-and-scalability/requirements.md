# Requirements Document

## Introduction

Phase 11 optimizes the Omni-Modal Enterprise Research Orchestrator for latency, throughput, and cost efficiency. The system currently processes ingestion synchronously on the HTTP handler thread (`BaseHTTPRequestHandler` in `main.py`), uses a single-connection pgvector query per request with no caching, and runs the ADK workflow sequentially within the request/response cycle. This phase introduces async background ingestion jobs, query-result caching, pgvector index tuning, batch embedding writes, and a benchmark harness — all driven by measured bottlenecks, not premature optimization.

## Glossary

- **API_Server**: The Python `BaseHTTPRequestHandler`-based HTTP server in `main.py` that handles all inbound requests.
- **Ingestion_Pipeline**: The `MultimodalIngestionPipeline` in `ingestion/pipeline.py` that extracts, normalizes, chunks, and embeds documents.
- **Background_Worker**: A dedicated thread or process that executes ingestion jobs off the main HTTP handler thread.
- **Job_Queue**: The persistent or in-memory queue used by the `Background_Worker` to receive and process `IngestionJob` items.
- **Retriever**: The `PgVectorChunkRetriever` in `qa/retrieval.py` that performs cosine-similarity vector search against the `embeddings` table.
- **Query_Cache**: An in-process or external cache that stores retrieval results keyed by a normalized query signature and tenant context.
- **ADK_Workflow**: The `DeterministicAgentGraph` / `InternalResearchAdkWorkflow` in `orchestration/adk_workflow.py` that orchestrates multi-step research queries.
- **Benchmark_Harness**: A standalone script that measures end-to-end latency and throughput for retrieval and ingestion under representative load.
- **HNSW_Index**: The `embeddings_vector_hnsw_idx` HNSW index on the `embeddings` table used by pgvector for approximate nearest-neighbour search.
- **Batch_Embedder**: A component that accumulates chunks from one or more documents and writes embeddings to the database in configurable-sized batches rather than one row per chunk.
- **Connection_Pool**: A pool of reusable database connections that eliminates per-request `psycopg.connect()` overhead.
- **Tenant**: A logical isolation boundary identified by `tenant_id`, used for multi-tenant data separation throughout the system.

---

## Requirements

### Requirement 1: Async Background Ingestion

**User Story:** As a researcher, I want to upload a document and receive an immediate acknowledgement, so that the UI is not blocked while large PDF or audio files are being processed.

#### Acceptance Criteria

1. WHEN a POST request is received at `/ingest/local`, THE API_Server SHALL enqueue the ingestion job and return a `202 Accepted` response containing the `job_id` within 200 ms of receiving the request, regardless of document size.
2. WHEN an ingestion job is enqueued, THE Background_Worker SHALL process the job asynchronously, independently of the HTTP response cycle.
3. WHILE the Background_Worker is processing a job, THE Job_Queue SHALL expose a status endpoint at `GET /ingest/jobs/{job_id}` that returns the current job status (`uploaded`, `processing`, `ready`, or `failed`) and, when complete, the ingestion result.
4. IF the Background_Worker encounters an unrecoverable error during processing, THEN THE Job_Queue SHALL mark the job status as `failed` and record the `error_code` and `error_message` in the job record.
5. WHEN the API_Server starts, THE Background_Worker SHALL start within the same process using a dedicated thread, requiring no external process manager.
6. THE Background_Worker SHALL process at most one job concurrently per worker thread to prevent resource exhaustion from simultaneous PDF extraction and audio transcription operations.

---

### Requirement 2: Vector Index Tuning

**User Story:** As a developer, I want the pgvector HNSW index configured with measured parameters, so that similarity search stays fast as the embeddings table grows.

#### Acceptance Criteria

1. THE HNSW_Index SHALL be created with `m` and `ef_construction` parameters that are documented with their measured impact on recall and query latency.
2. WHEN a vector similarity search query is executed, THE Retriever SHALL complete the query within 500 ms at the 95th percentile for a corpus of up to 100,000 embedding rows per tenant.
3. THE database schema migration SHALL set `hnsw.ef_search` to a value that achieves at least 90% recall against exact nearest-neighbour results, as verified by the Benchmark_Harness.
4. WHERE the `embeddings` table contains more than 10,000 rows for a given `tenant_id`, THE HNSW_Index SHALL be used for the cosine-similarity ordering clause (`embedding <=> %s::vector`) in the retrieval query.
5. THE database schema SHALL include a covering index on `(tenant_id, document_id)` on the `embeddings` table to eliminate the join-time heap fetch for the tenant filter.

---

### Requirement 3: Database Connection Pooling

**User Story:** As a developer, I want the retriever to reuse database connections, so that per-request TCP handshake overhead does not inflate query latency.

#### Acceptance Criteria

1. THE Retriever SHALL acquire connections from a Connection_Pool rather than calling `psycopg.connect()` per request.
2. THE Connection_Pool SHALL maintain a minimum of 2 and a maximum of 10 connections by default, configurable via environment variables `DB_POOL_MIN` and `DB_POOL_MAX`.
3. WHEN all connections in the Connection_Pool are in use, THE Retriever SHALL wait up to 5 seconds for a connection to become available before raising a timeout error.
4. IF a connection in the Connection_Pool is broken or stale, THEN THE Connection_Pool SHALL replace it transparently without propagating the error to the caller.
5. THE Connection_Pool SHALL be initialised once at API_Server startup and shared across all request handler instances.

---

### Requirement 4: Query Result Caching

**User Story:** As a researcher, I want repeated identical queries to return quickly, so that exploring the same topic across multiple sessions does not incur repeated embedding and database round-trips.

#### Acceptance Criteria

1. WHEN the Retriever receives a query, THE Query_Cache SHALL be checked using a cache key derived from the SHA-256 hash of the normalized question text, `tenant_id`, `top_k`, and `min_similarity`.
2. WHEN a cache hit occurs, THE Retriever SHALL return the cached `list[RetrievedChunk]` without executing a database query, and the response time SHALL be under 50 ms.
3. WHEN a cache miss occurs, THE Retriever SHALL execute the vector search, store the result in the Query_Cache with a configurable TTL (default 300 seconds), and return the result.
4. WHEN a new document is successfully ingested for a given `tenant_id`, THE Query_Cache SHALL evict all cached entries for that `tenant_id` to prevent stale results.
5. THE Query_Cache SHALL be an in-process LRU cache with a configurable maximum of 256 entries by default, requiring no external cache service.
6. WHERE an environment variable `QUERY_CACHE_ENABLED=false` is set, THE Query_Cache SHALL be disabled and THE Retriever SHALL always execute a live database query.

---

### Requirement 5: Batch Embedding Writes

**User Story:** As a developer, I want chunks to be written to the database in batches rather than one at a time, so that ingestion throughput scales with document size without proportional database round-trips.

#### Acceptance Criteria

1. WHEN the Ingestion_Pipeline produces chunks for a document, THE Batch_Embedder SHALL group the chunks into batches of configurable size (default 64 chunks per batch) before writing to the `embeddings` and `document_chunks` tables.
2. WHEN a batch insert completes, THE Batch_Embedder SHALL verify that the number of rows inserted equals the number of chunks submitted in that batch.
3. IF a batch insert fails due to a unique-constraint violation on `document_chunks(document_id, chunk_index)`, THEN THE Batch_Embedder SHALL use an upsert (`INSERT … ON CONFLICT DO UPDATE`) strategy to handle re-ingestion of the same document idempotently.
4. THE Batch_Embedder SHALL execute all batch inserts for a single document within a single database transaction so that a partial failure leaves no orphaned chunks.
5. WHEN the batch size is set to 1, THE Batch_Embedder SHALL behave identically to the per-chunk insert path, preserving correctness under all configurations.

---

### Requirement 6: Benchmark Harness

**User Story:** As a developer, I want a repeatable benchmark script, so that I can measure the actual latency and throughput of retrieval and ingestion before and after optimizations.

#### Acceptance Criteria

1. THE Benchmark_Harness SHALL measure end-to-end retrieval latency (p50, p95, p99) for a configurable number of queries (default 100) against a seeded corpus.
2. THE Benchmark_Harness SHALL measure ingestion throughput in documents per minute for a configurable batch of test files (default 10 PDF files).
3. WHEN the Benchmark_Harness completes a run, THE Benchmark_Harness SHALL write results to a JSON file at a configurable output path with fields: `timestamp`, `retrieval_p50_ms`, `retrieval_p95_ms`, `retrieval_p99_ms`, and `ingestion_docs_per_minute`.
4. THE Benchmark_Harness SHALL be executable as a standalone Python script (`python -m omni_modal.benchmark`) and require only the standard `DATABASE_URL` environment variable to run.
5. THE Benchmark_Harness SHALL include a baseline run output committed to the repository so that future runs can be compared against a known baseline.

---

### Requirement 7: Non-Blocking Query Pipeline

**User Story:** As a researcher, I want my query to complete without being delayed by another user's concurrent ingestion job, so that research queries remain responsive under load.

#### Acceptance Criteria

1. WHILE the Background_Worker is executing an ingestion job, THE API_Server SHALL continue to accept and process query requests on the main handler thread without queuing delay.
2. WHEN 5 concurrent query requests are submitted simultaneously, THE API_Server SHALL begin processing all 5 within 100 ms of receipt, without requiring the previous request to complete first.
3. THE ADK_Workflow step timeout SHALL be set to 30,000 ms and the total workflow timeout SHALL be set to 120,000 ms, and THE API_Server SHALL return an HTTP 504 response to the caller when the total timeout is exceeded.
4. THE API_Server SHALL enforce a per-tenant in-flight query limit of 10 concurrent queries, returning HTTP 429 when the limit is exceeded.

---

### Requirement 8: Stability Under Multi-Document and Multi-Query Load

**User Story:** As a system operator, I want the application to remain stable when multiple documents are ingested and multiple queries are running concurrently, so that research sessions are not interrupted by resource exhaustion.

#### Acceptance Criteria

1. WHEN 20 documents are ingested sequentially via the Job_Queue, THE API_Server SHALL remain responsive to health checks (`GET /health`) throughout, with each health check returning `200 OK` within 500 ms.
2. WHEN the `embeddings` table contains 50,000 rows across 10 tenants, THE Retriever SHALL return results within the latency targets defined in Requirement 2.
3. IF the Background_Worker thread terminates unexpectedly, THEN THE API_Server SHALL log the failure via the observability module and restart the Background_Worker thread within 5 seconds.
4. THE Ingestion_Pipeline SHALL not hold a database connection for longer than the duration of a single batch write operation, returning the connection to the Connection_Pool between stages.
5. WHEN the API_Server has been running for 1 hour with continuous ingestion and query traffic, THE Connection_Pool SHALL not show a monotonically increasing active connection count (no connection leak).
