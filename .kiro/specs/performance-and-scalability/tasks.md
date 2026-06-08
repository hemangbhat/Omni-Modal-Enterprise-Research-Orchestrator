# Implementation Plan: Performance and Scalability (Phase 11)

## Overview

Five targeted optimisations are introduced in order of dependency: the `ConnectionPool` singleton and `QueryCache` are built first (no dependencies), then `BatchEmbedder` (needs pool), then `BackgroundWorker` + `AsyncIngestionQueue` (needs pipeline), then the retriever is updated to accept pool and cache, then `main.py` wires everything together, then the HNSW migration is applied, and finally the benchmark harness is built. Property-based tests (Hypothesis) are placed immediately after the component they validate.

---

## Tasks

- [ ] 1. Set up `ConnectionPool` singleton (`db/pool.py`)
  - [ ] 1.1 Create `services/api/src/omni_modal/db/__init__.py` (empty) and `services/api/src/omni_modal/db/pool.py`
    - Implement `get_connection_pool()` with double-checked locking (`_pool_lock`) that reads `DATABASE_URL`, `DB_POOL_MIN` (default 2), `DB_POOL_MAX` (default 10) from env and constructs a `psycopg_pool.ConnectionPool` singleton
    - Implement `_on_reconnect_failed()` that calls `observability.capture_message(…, level="error")`
    - Implement `close_connection_pool()` that sets `_pool = None` after closing
    - Raise `RuntimeError` on missing `DATABASE_URL`
    - _Requirements: 3.2, 3.3, 3.4, 3.5_

  - [ ]* 1.2 Write property test for `ConnectionPool` singleton identity (Property 3)
    - **Property 3: Connection pool singleton identity**
    - **Validates: Requirements 3.5**
    - File: `services/api/tests/performance/test_connection_pool.py`
    - Use `@given(st.integers(min_value=1, max_value=50))` for call count
    - Mock `psycopg_pool.ConnectionPool` to avoid real DB; assert `get_connection_pool()` returns same object on all calls

  - [ ]* 1.3 Write property test for pool env-configured sizes (Property 4)
    - **Property 4: Pool constructed with env-configured sizes**
    - **Validates: Requirements 3.2**
    - File: `services/api/tests/performance/test_connection_pool.py`
    - Use `@given(st.integers(1, 5), st.integers(6, 20))` for `(min_size, max_size)`
    - Monkeypatch env vars and `_pool = None`; assert `ConnectionPool` constructor called with correct args

- [ ] 2. Implement `QueryCache` (`qa/cache.py`)
  - [ ] 2.1 Create `services/api/src/omni_modal/qa/cache.py`
    - Implement `QueryCache.__init__` reading `QUERY_CACHE_ENABLED` env var (default `true`)
    - Implement `compute_key()` static method: lowercase-strip question, `json.dumps` canonical tuple, SHA-256 hex digest
    - Implement thread-safe `get()` / `set()` using `threading.Lock` wrapping `cachetools.TTLCache`
    - Implement `evict_tenant()` using `_tenant_keys: dict[str, set[CacheKey]]` secondary index; return evicted count
    - Implement `__len__()` returning current cache size; catch internal `cachetools` exceptions and log rather than raise
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 2.2 Write property test for cache key determinism and collision resistance (Property 5)
    - **Property 5: Cache key determinism and collision resistance**
    - **Validates: Requirements 4.1**
    - File: `services/api/tests/performance/test_query_cache.py`
    - Use `@given(st.text(), st.text(), st.integers(1, 50), st.floats(0.0, 1.0, allow_nan=False))`
    - Assert same inputs → same key; assert distinct inputs (any field differs) → distinct keys

  - [ ]* 2.3 Write property test for cache hit bypasses DB (Property 6)
    - **Property 6: Cache hit bypasses the database**
    - **Validates: Requirements 4.2**
    - File: `services/api/tests/performance/test_query_cache.py`
    - Pre-seed cache; assert `pool.connection()` never called when `cache.get()` returns a value

  - [ ]* 2.4 Write property test for cache miss stores result round-trip (Property 7)
    - **Property 7: Cache miss stores result (round-trip)**
    - **Validates: Requirements 4.3**
    - File: `services/api/tests/performance/test_query_cache.py`
    - Use mock DB returning generated chunks; assert `cache.get(key)` equals `retrieve()` result after a miss

  - [ ]* 2.5 Write property test for tenant cache eviction completeness (Property 8)
    - **Property 8: Tenant cache eviction is complete**
    - **Validates: Requirements 4.4**
    - File: `services/api/tests/performance/test_query_cache.py`
    - Use `@given(st.text(), st.lists(st.text(), min_size=1))`; after `evict_tenant(T)` assert all keys for T return `None`

  - [ ]* 2.6 Write property test for cache size bounded by maxsize (Property 9)
    - **Property 9: Cache size is bounded by maxsize**
    - **Validates: Requirements 4.5**
    - File: `services/api/tests/performance/test_query_cache.py`
    - Use `@given(st.integers(1, 512), st.integers(1, 1000))`; assert `len(cache) <= maxsize` after all inserts

- [ ] 3. Implement `BatchEmbedder` (`ingestion/batch_embedder.py`)
  - [ ] 3.1 Create `services/api/src/omni_modal/ingestion/batch_embedder.py`
    - Implement `BatchEmbedder.__init__` accepting `pool: ConnectionPool`, `batch_size=64`, `embedding_model`, `dimensions`
    - Implement `_batches()` splitting a sequence into sublists of at most `batch_size`
    - Implement `_insert_chunk_batch()` executing the `document_chunks` upsert with `ON CONFLICT (document_id, chunk_index) DO UPDATE`; raise `BatchInsertError` if `cursor.rowcount != len(batch)`
    - Implement `_insert_embedding_batch()` executing the `embeddings` upsert with `ON CONFLICT (chunk_id) DO UPDATE`; raise `BatchInsertError` on mismatch
    - Implement `write_chunks()` acquiring one connection from pool, opening a single transaction, iterating `_batches()` for both chunk and embedding inserts, rolling back on any error
    - Define `BatchInsertError` in the same file
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 3.2 Write property test for batch partitioning correctness (Property 10)
    - **Property 10: Batch partitioning correctness**
    - **Validates: Requirements 5.1**
    - File: `services/api/tests/performance/test_batch_embedder.py`
    - Use `@given(st.lists(st.integers()), st.integers(1, 256))`
    - Assert: `ceil(N/B)` sublists, each `<= B`, concatenation equals original

  - [ ]* 3.3 Write property test for batch insert row-count invariant (Property 11)
    - **Property 11: Batch insert row-count invariant**
    - **Validates: Requirements 5.2**
    - File: `services/api/tests/performance/test_batch_embedder.py`
    - Use `@given(st.lists(st.builds(StructuredChunk, …), min_size=1, max_size=200))`
    - Mock cursor with `rowcount = len(batch)`; assert no `BatchInsertError` raised

  - [ ]* 3.4 Write property test for idempotent re-ingestion (Property 12)
    - **Property 12: Idempotent re-ingestion (upsert)**
    - **Validates: Requirements 5.3, 5.5**
    - File: `services/api/tests/performance/test_batch_embedder.py`
    - Call `write_chunks()` twice with same chunk list against an in-memory SQLite (or mock); assert final state identical to single write

  - [ ]* 3.5 Write property test for connection released per batch (Property 16)
    - **Property 16: Connection not held across pipeline stages**
    - **Validates: Requirements 8.4**
    - File: `services/api/tests/performance/test_batch_embedder.py`
    - Use `@given(st.lists(st.builds(StructuredChunk, …), min_size=1))`
    - Mock pool context manager; assert connection context manager entered and exited exactly once per `write_chunks()` call

- [ ] 4. Implement `BackgroundWorker` and `AsyncIngestionQueue`
  - [ ] 4.1 Create `services/api/src/omni_modal/ingestion/worker.py`
    - Implement `BackgroundWorker.__init__` storing `pipeline`, `job_queue`, `jobs_store`, `watchdog_callback`; create `threading.Event` for `_stop_event`
    - Implement `start()` creating `daemon=True` thread running `_run()`; idempotent (check `_thread.is_alive()`)
    - Implement `_run()` loop: `queue.get(timeout=1.0)` with `queue.Empty` continue; call `_process_one(job)`; check `_stop_event` each iteration
    - Implement `_process_one()`: transition job to `"processing"`, call `pipeline.ingest()`, transition to `"ready"` or `"failed"` under lock; catch all exceptions and mark job `"failed"` with `EXTRACTION_FAILED`
    - Implement `stop(timeout=5.0)` setting `_stop_event` and calling `_thread.join(timeout)`
    - _Requirements: 1.2, 1.4, 1.5, 1.6, 8.3_

  - [ ] 4.2 Create `services/api/src/omni_modal/ingestion/async_queue.py`
    - Implement `AsyncIngestionQueue.__init__` creating `queue.Queue(maxsize=max_queue_size)` and `threading.Lock` for `_jobs` dict
    - Implement `enqueue()` creating `IngestionJob(status="uploaded")`, storing under lock, putting on queue, returning immediately (raises `queue.Full` if bounded queue is full)
    - Implement thread-safe `get()` and `fail()` methods matching `InMemoryIngestionQueue` interface
    - Implement `start_worker()` creating and starting a `BackgroundWorker`, wiring watchdog callback that logs via observability and calls `start()` again
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6_

  - [ ]* 4.3 Write property test for job failure records full error context (Property 1)
    - **Property 1: Job failure records full error context**
    - **Validates: Requirements 1.4**
    - File: `services/api/tests/performance/test_background_worker.py`
    - Use `@given(st.sampled_from(IngestionErrorCode), st.text(min_size=1))`
    - Assert `fail()` produces job with `status=="failed"`, matching `error_code` and `error_message`

  - [ ]* 4.4 Write property test for at-most-one-concurrent job (Property 2)
    - **Property 2: BackgroundWorker processes at most one job at a time**
    - **Validates: Requirements 1.6**
    - File: `services/api/tests/performance/test_background_worker.py`
    - Use `@given(st.lists(st.builds(IngestionJob, …), min_size=1, max_size=20))`
    - Use a slow mock pipeline and poll `_jobs_store`; assert at most 1 job in `"processing"` at any snapshot

  - [ ]* 4.5 Write property test for watchdog restarts crashed worker (Property 15)
    - **Property 15: Watchdog restarts crashed worker**
    - **Validates: Requirements 8.3**
    - File: `services/api/tests/performance/test_background_worker.py`
    - Simulate unexpected thread death via mock; assert watchdog callback invoked and new thread alive within 5 s

- [ ] 5. Update `PgVectorChunkRetriever` to accept pool and cache
  - [ ] 5.1 Modify `services/api/src/omni_modal/qa/retrieval.py`
    - Add `pool: "ConnectionPool | None" = None` and `cache: "QueryCache | None" = None` to `PgVectorChunkRetriever.__init__`; store as `self._pool` and `self._cache`
    - In `retrieve()`, add cache check at the top: compute key, call `self._cache.get(key)`, return immediately on hit
    - Replace `psycopg.connect(self._database_url, …)` with `self._pool.connection()` context manager when `self._pool` is not `None`
    - After successful DB query write results to cache with `self._cache.set(key, results)` when cache is set
    - Keep existing `psycopg.connect()` fallback path when `pool` is `None` (backwards compatibility)
    - _Requirements: 3.1, 4.1, 4.2, 4.3, 4.6_

- [ ] 6. Wire all components in `main.py`
  - [ ] 6.1 Modify `services/api/src/omni_modal/main.py`
    - Import `get_connection_pool`, `close_connection_pool` from `omni_modal.db.pool`
    - Import `QueryCache` from `omni_modal.qa.cache`
    - Import `AsyncIngestionQueue` from `omni_modal.ingestion.async_queue`
    - At server class level: call `get_connection_pool()` to initialise pool; construct `QueryCache(enabled=os.environ.get("QUERY_CACHE_ENABLED","true").lower()!="false")`
    - Replace `InMemoryIngestionQueue` with `AsyncIngestionQueue`; call `queue.start_worker()`
    - Inject `pool=` and `cache=` into `PgVectorChunkRetriever` constructor
    - Register `close_connection_pool` with `atexit`
    - Update `/ingest/local` handler to return `202 Accepted` with `{"job_id": …}` immediately (do not wait for processing)
    - Add `GET /ingest/jobs/{job_id}` route returning `queue.get(job_id)` serialised as JSON; return 404 if not found
    - After successful ingestion completion in worker, call `cache.evict_tenant(tenant_id)` (wired via watchdog / job completion callback)
    - _Requirements: 1.1, 1.3, 1.5, 3.5, 4.4, 4.6, 7.3, 8.3_

- [ ] 7. Checkpoint — core integration
  - Ensure all tests pass, ask the user if questions arise.
  - Run `python -m unittest discover -s services/api/tests`

- [ ] 8. Add HNSW tuning migration
  - [ ] 8.1 Create `packages/db/drizzle/0002_perf.sql`
    - `DROP INDEX IF EXISTS embeddings_vector_hnsw_idx`
    - `CREATE INDEX embeddings_vector_hnsw_idx ON embeddings USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)`
    - `ALTER DATABASE current_database() SET hnsw.ef_search = 40`
    - `CREATE INDEX IF NOT EXISTS embeddings_tenant_doc_covering_idx ON embeddings (tenant_id, document_id) INCLUDE (chunk_id)`
    - Add file header comment documenting measured baseline recall and latency
    - _Requirements: 2.1, 2.3, 2.4, 2.5_

- [ ] 9. Implement benchmark harness (`benchmark/__main__.py`)
  - [ ] 9.1 Create `services/api/src/omni_modal/benchmark/__init__.py` (empty) and `services/api/src/omni_modal/benchmark/__main__.py`
    - Implement `BenchmarkStats` dataclass with fields: `timestamp` (ISO-8601 UTC), `retrieval_p50_ms`, `retrieval_p95_ms`, `retrieval_p99_ms`, `ingestion_docs_per_minute`
    - Implement `serialise_stats()` returning a `dict` with exactly those 5 keys
    - Implement `run_retrieval_benchmark()`: execute queries sequentially via `ChunkRetriever.retrieve()`, collect wall-clock latencies, compute `statistics.quantiles` for p50/p95/p99, return `BenchmarkStats`
    - Implement `run_ingestion_benchmark()`: enqueue test files via `AsyncIngestionQueue.enqueue()`, measure elapsed time, return docs/min
    - Implement `main()` with `argparse` (`--queries 100`, `--docs 10`, `--output results.json`); write JSON atomically (write to `.tmp` then rename); exit non-zero on error without writing partial file
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ]* 9.2 Write property test for benchmark result serialisation completeness (Property 13)
    - **Property 13: Benchmark result serialisation completeness**
    - **Validates: Requirements 6.3**
    - File: `services/api/tests/performance/test_benchmark_serialise.py`
    - Use `@given(st.builds(BenchmarkStats, timestamp=st.text(), retrieval_p50_ms=st.floats(0,allow_nan=False), …))`
    - Assert returned dict has exactly the 5 required keys with correct Python types

- [ ] 10. Add ADK workflow timeout property test
  - [ ]* 10.1 Write property test for workflow total timeout enforcement (Property 14)
    - **Property 14: Workflow total timeout is enforced**
    - **Validates: Requirements 7.3**
    - File: `services/api/tests/performance/test_adk_timeout.py`
    - Use `@given(st.floats(0.001, 1.0))` for step durations summing beyond `total_timeout_ms`
    - Assert `DeterministicAgentGraph` (or mock) raises `TimeoutError` before returning a result

- [ ] 11. Add integration tests
  - [ ]* 11.1 Write integration test: `/health` responsive during background ingestion
    - File: `services/api/tests/performance/integration/test_health_during_ingest.py`
    - Start slow mock ingestion job on `AsyncIngestionQueue`; while job runs, send `/health` request and assert `200 OK` within 500 ms
    - _Requirements: 7.1, 8.1_

  - [ ]* 11.2 Write integration test: retriever uses pool not `psycopg.connect`
    - File: `services/api/tests/performance/integration/test_retrieval_latency.py`
    - Inject mock pool into `PgVectorChunkRetriever`; assert `pool.connection()` called and `psycopg.connect` never called
    - _Requirements: 3.1_

- [ ] 12. Final checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Run `python -m unittest discover -s services/api/tests`

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Two checkpoints ensure incremental validation after core integration and at completion
- All 16 correctness properties from the design document are covered by Hypothesis PBT sub-tasks (Properties 1–16)
- `python -m unittest discover -s services/api/tests` is the test runner for this project
- `psycopg_pool` and `cachetools` must be added to `services/api/pyproject.toml` dependencies before tasks 1 and 2 are executed
- Feature-flag backwards compatibility: `QUERY_CACHE_ENABLED=false` disables the cache; `DB_POOL_MIN`/`DB_POOL_MAX` tune the pool; existing tests continue to pass when env vars are absent

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.2", "2.3", "2.4", "2.5", "2.6"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "3.5", "4.1"] },
    { "id": 4, "tasks": ["4.2"] },
    { "id": 5, "tasks": ["4.3", "4.4", "4.5", "5.1"] },
    { "id": 6, "tasks": ["6.1"] },
    { "id": 7, "tasks": ["8.1", "9.1"] },
    { "id": 8, "tasks": ["9.2", "10.1"] },
    { "id": 9, "tasks": ["11.1", "11.2"] }
  ]
}
```
