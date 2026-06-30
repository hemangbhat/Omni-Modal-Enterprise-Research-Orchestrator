from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import tempfile
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import atexit

# Load the repository .env BEFORE importing anything that reads os.environ at
# import time (observability, embedding/DB selection, JWT secret, etc.), but
# ONLY when this module is launched as the server entry point
# (`python -m omni_modal.main`). When imported as a module (e.g. by tests),
# the caller controls the environment explicitly, so we must not clobber it.
# Process environment variables always take precedence over .env values.
if __name__ == "__main__":
    from omni_modal.env_loader import load_dotenv

    load_dotenv()

from omni_modal.observability import observability
observability.init()
atexit.register(observability.flush)
from omni_modal.ingestion import InMemoryIngestionQueue, MultimodalIngestionPipeline
from omni_modal.ingestion.async_queue import AsyncIngestionQueue
from omni_modal.ingestion.redis_queue import select_ingestion_queue
from omni_modal.ingestion.extractors import LocalWhisperTranscriber
from omni_modal.ingestion.http_contract import (
    IngestionContractError,
    ingestion_request_from_payload,
)
from omni_modal.ingestion.pipeline import serialize_ingestion_result
from omni_modal.entity_extraction.service import EntityExtractionService
from omni_modal.orchestration.a2a import external_client_from_environment
from omni_modal.orchestration import InternalResearchAdkWorkflow
from omni_modal.orchestration import Phase1Orchestrator
from omni_modal.qa import (
    HashingQueryEmbeddingProvider,
    PgVectorChunkRetriever,
    QueryContractError,
    query_request_from_payload,
    select_answer_synthesizer,
    select_embedding_provider,
    stream_markdown,
)
from omni_modal.qa.in_memory_store import (
    InMemoryChunkPersistence,
    InMemoryChunkRetriever,
    InMemoryVectorStore,
)
from omni_modal.qa.cache import QueryCache
from omni_modal.qa.redis_cache import select_query_cache
from omni_modal.db.pool import get_connection_pool, close_connection_pool, reset_pool_for_testing
from omni_modal.security.auth import verify_jwt, jwt_secret_from_env, AuthError, JwtClaims, _make_jwt
from omni_modal.security.accounts import AccountError, get_account_service
from omni_modal.security.sessions import select_session_service, RefreshTokenError
from omni_modal.security.rbac import assert_endpoint_roles, RbacError
from omni_modal.security.audit import InMemoryAuditSink
from omni_modal.security.pg_audit_sink import select_audit_sink
from omni_modal.security.rate_limiting import SlidingWindowRateLimiter, RateLimitExceeded
from omni_modal.security.redis_rate_limiter import select_rate_limiter
from omni_modal.security.input_validation import (
    ValidationError, assert_body_size, assert_query_length, assert_tenant_id, assert_document_id_uuid,
    MAX_BODY_BYTES,
)
from omni_modal.mcp.models import ToolContext
from omni_modal.saas import get_saas_service
from omni_modal.saas.plans import PlanLimitExceeded


class OmniModalHandler(BaseHTTPRequestHandler):
    # Redis-backed when REDIS_URL is set (shared across instances), else the
    # in-process LRU+TTL cache. Same interface either way.
    _query_cache = select_query_cache()
    # Try to get connection pool if DATABASE_URL is set
    try:
        _connection_pool = get_connection_pool() if os.environ.get("DATABASE_URL") else None
    except Exception:
        _connection_pool = None

    # Shared embedding provider so ingestion and retrieval produce comparable
    # vectors. Backend is selected from EMBEDDING_BACKEND (default: hashing
    # fallback), falling back to hashing if a real backend is unavailable.
    _embedding_selection = select_embedding_provider()
    _embedding_provider = _embedding_selection.provider
    try:
        observability.add_breadcrumb(
            message="Embedding backend selected",
            category="embeddings",
            level="info",
            data={
                "backend": _embedding_selection.backend,
                "requested_backend": _embedding_selection.requested_backend,
                "fell_back": _embedding_selection.fell_back,
                "dimensions": getattr(_embedding_provider, "dimensions", None),
            },
        )
    except Exception:
        pass

    # Whisper: use WHISPER_MODEL_PATH as the model name/size (e.g. "base", "small",
    # or an absolute path). Falls back to the default if unset.
    _whisper_model = os.environ.get("WHISPER_MODEL_PATH", "").strip() or None
    _audio_transcriber = LocalWhisperTranscriber(model=_whisper_model) if _whisper_model else None

    if _connection_pool is not None or os.environ.get("DATABASE_URL"):
        # Postgres + pgvector path. Ingestion now persists end-to-end:
        # documents → document_chunks → embeddings (vector), so the pgvector
        # retriever performs genuine semantic search over ingested content.
        _vector_store = None
        _retriever = PgVectorChunkRetriever(
            _embedding_provider,
            pool=_connection_pool,
            cache=_query_cache,
        )
        from omni_modal.qa.pg_persistence import PostgresChunkPersistence  # noqa: PLC0415

        _ingestion_pipeline = MultimodalIngestionPipeline(
            audio_transcriber=_audio_transcriber,
            persistence=PostgresChunkPersistence(
                _embedding_provider,
                pool=_connection_pool,
                database_url=os.environ.get("DATABASE_URL"),
                embedding_model=_embedding_selection.backend,
                dimensions=getattr(_embedding_provider, "dimensions", 384),
            ),
        )
    else:
        # Local, Postgres-free demo path: a single in-memory vector store is
        # shared between ingestion persistence and retrieval, giving a genuine
        # end-to-end flow (upload -> persist -> retrieve) without a database.
        _vector_store = InMemoryVectorStore()
        _retriever = InMemoryChunkRetriever(_embedding_provider, _vector_store)
        _ingestion_pipeline = MultimodalIngestionPipeline(
            audio_transcriber=_audio_transcriber,
            persistence=InMemoryChunkPersistence(_vector_store, _embedding_provider),
        )

    # Entity extraction service — enriches chunks post-ingestion.
    # Uses QLORA_ENTITY_MODEL_PATH to select backend (rule-based if unset).
    _entity_service = EntityExtractionService()

    queue = select_ingestion_queue(
        _ingestion_pipeline,
        cache_evict_callback=_query_cache.evict_tenant,
        entity_service=_entity_service,
    )
    research_workflow = InternalResearchAdkWorkflow(
        _retriever,
        external_client_from_environment(),
        select_answer_synthesizer(),
    )
    _audit_sink = select_audit_sink()
    # Redis-backed when REDIS_URL is set (distributed), else in-process.
    _rate_limiter = select_rate_limiter()
    _account_service = get_account_service()
    # Access/refresh token lifecycle (rotation + revocation). Redis-backed when
    # REDIS_URL is set, else in-process.
    _session_service = select_session_service()

    # SaaS layer: orgs/workspaces, usage metering, notifications, billing (demo),
    # and optional adapters. Seeded with a demo org so the UI is never empty.
    _saas = get_saas_service()

    # ── Startup log — printed to stdout so it's visible in logs / Docker ─────
    try:
        _active_path = "pgvector (Neon/Postgres)" if (_connection_pool is not None or os.environ.get("DATABASE_URL")) else "in-memory (local demo)"
        _embed_backend = _embedding_selection.backend
        _embed_dims = getattr(_embedding_provider, "dimensions", "unknown")
        _fell_back = " [FELL BACK TO HASHING]" if _embedding_selection.fell_back else ""
        import sys as _sys  # noqa: PLC0415
        print(
            f"\n{'─'*60}\n"
            f"  OMERO Backend starting\n"
            f"  Retrieval path : {_active_path}\n"
            f"  Embedding      : {_embed_backend} ({_embed_dims}-dim){_fell_back}\n"
            f"  Whisper model  : {os.environ.get('WHISPER_MODEL_PATH', 'not set')}\n"
            f"  NER model      : {os.environ.get('ENTITY_NER_MODEL_PATH') or os.environ.get('QLORA_ENTITY_MODEL_PATH', 'rule-based (no model)')}\n"
            f"  Sentry         : {'enabled' if os.environ.get('SENTRY_DSN') else 'disabled'}\n"
            f"{'─'*60}\n",
            file=_sys.stderr,
            flush=True,
        )
    except Exception:
        pass

    def _get_trace_headers(self) -> dict[str, str]:
        """Extract sentry-trace and baggage headers from the request."""
        headers: dict[str, str] = {}
        sentry_trace = self.headers.get("sentry-trace", "")
        baggage = self.headers.get("baggage", "")
        if sentry_trace:
            headers["sentry-trace"] = sentry_trace
        if baggage:
            headers["baggage"] = baggage
        return headers

    def _get_client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "unknown"

    def _authenticate(self) -> JwtClaims | None:
        """Verify bearer token; return JwtClaims or write 401 and return None."""
        if self.path == "/health":
            return None  # health is public — no claims needed

        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._write_json(401, {"error": "Missing or invalid bearer token."})
            try:
                secret = jwt_secret_from_env()
            except RuntimeError:
                secret = "not-configured"
            self._audit_sink.record_event(
                None, "auth:failure", "endpoint", self.path, "denied",
                {"failure_reason": "missing_bearer_token", "path": self.path}
            )
            return None

        token = auth_header[len("Bearer "):]
        try:
            secret = jwt_secret_from_env()
            claims = verify_jwt(token, secret)
            return claims
        except (AuthError, RuntimeError) as exc:
            self._write_json(401, {"error": f"Authentication failed: {exc}"})
            self._audit_sink.record_event(
                None, "auth:failure", "endpoint", self.path, "denied",
                {"failure_reason": str(exc), "path": self.path}
            )
            return None

    def do_GET(self) -> None:
        trace_headers = self._get_trace_headers()
        with observability.continue_trace(trace_headers):
            # Rate limiting for non-health paths
            if self.path != "/health":
                claims = self._authenticate()
                if claims is None:
                    return
                try:
                    self._rate_limiter.check_tenant(claims.tenant_id)
                    self._rate_limiter.check_user(claims.tenant_id, claims.user_id)
                except RateLimitExceeded as exc:
                    self._write_json(429, {"error": f"Rate limit exceeded. Retry after {exc.retry_after}s."})
                    self.send_header("Retry-After", str(exc.retry_after))
                    self._audit_sink.record_event(
                        ToolContext(tenant_id=claims.tenant_id, actor_user_id=claims.user_id),
                        "rate_limit", "endpoint", self.path, "rate_limited",
                        {"endpoint": self.path, "retry_after": exc.retry_after}
                    )
                    return

            try:
                if self.path.startswith("/ingest/jobs/"):
                    job_id = self.path[len("/ingest/jobs/"):]
                    job = self.queue.get(job_id)
                    if job is None:
                        self._write_json(404, {"error": f"Job {job_id} not found."})
                        return
                    result_data = None
                    if job.result is not None:
                        from omni_modal.ingestion.pipeline import serialize_ingestion_result as _ser  # noqa: PLC0415
                        result_data = _ser(job.result)
                    self._write_json(200, {
                        "job_id": job.id,
                        "status": job.status,
                        "result": result_data,
                        "error_code": job.error_code.value if job.error_code else None,
                        "error_message": job.error_message,
                    })
                    return
                # Split path and query string for the remaining routes.
                from urllib.parse import urlparse, parse_qs  # noqa: PLC0415
                parsed = urlparse(self.path)
                path_only = parsed.path
                query = parse_qs(parsed.query)
                workspace_id = (query.get("workspace_id") or [None])[0]
                if path_only == "/documents":
                    self._handle_list_documents(claims=claims, workspace_id=workspace_id)
                    return
                if path_only.startswith("/entities/"):
                    document_id = path_only[len("/entities/"):]
                    self._handle_list_entities(document_id=document_id, claims=claims)
                    return
                if path_only == "/projects":
                    self._handle_list_projects(claims=claims, workspace_id=workspace_id)
                    return
                if path_only == "/archives":
                    self._handle_list_archives(claims=claims, workspace_id=workspace_id)
                    return
                if path_only == "/workspaces":
                    self._handle_list_workspaces(claims=claims)
                    return
                if path_only == "/usage":
                    self._handle_usage(claims=claims)
                    return
                if path_only == "/members":
                    self._handle_list_members(claims=claims)
                    return
                if path_only == "/notifications":
                    self._handle_list_notifications(claims=claims)
                    return
                if path_only == "/plans":
                    self._handle_list_plans(claims=claims)
                    return
                if path_only == "/billing":
                    self._handle_billing(claims=claims)
                    return
                if path_only == "/invites/preview":
                    self._handle_preview_invite(token=(query.get("token") or [""])[0])
                    return
                if path_only == "/admin/stats":
                    self._handle_admin_stats(claims=claims)
                    return
                if self.path != "/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                self._write_json(200, Phase1Orchestrator().health())
            except Exception as exc:
                observability.capture_exception(exc, operation="do_GET")
                self.send_response(500)
                self.end_headers()
                self._write_json(500, {"status": "error", "error": str(exc)})

    def do_POST(self) -> None:
        trace_headers = self._get_trace_headers()
        with observability.continue_trace(trace_headers):
            # Stripe webhook is unauthenticated (Stripe can't send a JWT); it is
            # verified by signature inside the handler. Must run before auth.
            if self.path == "/billing/webhook":
                self._handle_stripe_webhook()
                return

            # Auth endpoints are unauthenticated (they mint the token). Must run
            # before _authenticate.
            if self.path == "/auth/register":
                self._handle_auth_register()
                return
            if self.path == "/auth/login":
                self._handle_auth_login()
                return
            if self.path == "/auth/refresh":
                self._handle_auth_refresh()
                return
            if self.path == "/auth/logout":
                self._handle_auth_logout()
                return

            # 1. Authentication
            claims = self._authenticate()
            if claims is None:
                return

            tool_context = ToolContext(
                tenant_id=claims.tenant_id,
                actor_user_id=claims.user_id,
                roles=claims.roles,
            )

            # 2. Rate limiting (before body parsing per Req 8.4)
            try:
                self._rate_limiter.check_tenant(claims.tenant_id)
                self._rate_limiter.check_user(claims.tenant_id, claims.user_id)
            except RateLimitExceeded as exc:
                self._write_json(429, {"error": f"Rate limit exceeded. Retry after {exc.retry_after}s."})
                self.send_header("Retry-After", str(exc.retry_after))
                self._audit_sink.record_event(
                    tool_context, "rate_limit", "endpoint", self.path, "rate_limited",
                    {"endpoint": self.path, "retry_after": exc.retry_after}
                )
                return

            # 3. Body size check (before reading body)
            content_length = int(self.headers.get("Content-Length", "0"))
            try:
                assert_body_size(content_length)
            except ValidationError as exc:
                self._write_json(413, {"error": str(exc)})
                return

            if self.path == "/query":
                self._handle_query(stream=False, claims=claims)
                return
            if self.path == "/query/stream":
                self._handle_query(stream=True, claims=claims)
                return
            if self.path == "/ingest/upload":
                self._handle_upload(claims=claims, tool_context=tool_context)
                return
            if self.path == "/workspaces":
                self._handle_create_workspace(claims=claims)
                return
            if self.path == "/invites":
                self._handle_create_invite(claims=claims)
                return
            if self.path == "/invites/accept":
                self._handle_accept_invite(claims=claims)
                return
            if self.path == "/billing/change-plan":
                self._handle_change_plan(claims=claims)
                return
            if self.path == "/billing/checkout":
                self._handle_billing_checkout(claims=claims)
                return
            if self.path == "/billing/confirm":
                self._handle_billing_confirm(claims=claims)
                return
            if self.path == "/billing/portal":
                self._handle_billing_portal(claims=claims)
                return
            if self.path == "/notifications/read":
                self._handle_mark_notifications_read(claims=claims)
                return
            if self.path != "/ingest/local":
                self.send_response(404)
                self.end_headers()
                return

            # 4. RBAC check for /ingest/local
            try:
                assert_endpoint_roles(self.path, claims.roles)
            except RbacError as exc:
                self._write_json(403, {"error": str(exc)})
                self._audit_sink.record_event(
                    tool_context, "access:denied", "endpoint", self.path, "denied",
                    {"failure_reason": str(exc), "path": self.path}
                )
                return

            try:
                with observability.child_span("ingestion", "document ingestion"):
                    payload = self._read_json_body()
                    # Input validation for document_id
                    if "document_id" in payload:
                        try:
                            assert_document_id_uuid(payload["document_id"])
                        except ValidationError as exc:
                            self._write_json(400, {"error": str(exc)})
                            return
                    request = ingestion_request_from_payload(payload)
                    job = self.queue.enqueue(request)

                # Audit the enqueue event
                self._audit_sink.record_event(
                    tool_context, "ingestion:enqueued", "document",
                    payload.get("document_id"),
                    job.status,
                    {
                        "job_id": job.id,
                        "document_id": payload.get("document_id"),
                        "tenant_id": claims.tenant_id,
                    }
                )

                self._write_json(202, {"job_id": job.id, "status": job.status})
            except IngestionContractError as exc:
                self._write_json(400, {"status": "failed", "error": str(exc)})
            except json.JSONDecodeError:
                self._write_json(400, {"status": "failed", "error": "Invalid JSON body."})
            except Exception as exc:
                observability.capture_exception(exc, operation="do_POST")
                self.send_response(500)
                self.end_headers()
                self._write_json(500, {"status": "error", "error": str(exc)})

    def _handle_list_projects(self, claims: JwtClaims | None, workspace_id: str | None = None) -> None:
        """GET /projects — return research projects derived from ingested documents.

        Groups documents by tenant and synthesises a project view. In production
        this would read from a projects table; here we derive projects from the
        documents present in the system so the page always shows real data.
        When ``workspace_id`` is set, only that workspace's documents are shown.
        """
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        ws_filter = self._saas.documents_in_workspace(workspace_id) if workspace_id else None
        projects: list[dict[str, object]] = []

        if self._vector_store is not None:
            # Derive projects from ingested document metadata
            doc_map: dict[str, dict[str, object]] = {}
            for chunk in self._vector_store.for_tenant(tenant_id):
                if ws_filter is not None and chunk.document_id not in ws_filter:
                    continue
                doc_id = chunk.document_id
                if doc_id not in doc_map:
                    doc_map[doc_id] = {
                        "id": doc_id,
                        "name": chunk.title,
                        "source_kind": chunk.source_type,
                        "chunk_count": 0,
                        "status": "active",
                    }
                doc_map[doc_id]["chunk_count"] = int(doc_map[doc_id]["chunk_count"]) + 1

            # Group documents as individual research projects
            for doc_id, doc in doc_map.items():
                projects.append({
                    "id": doc_id,
                    "code": f"PRJ-{doc_id[:8].upper()}",
                    "name": str(doc["name"]),
                    "icon": "science" if doc["source_kind"] == "audio" else "description",
                    "status": "Active",
                    "priority": "Medium",
                    "source_kind": doc["source_kind"],
                    "chunk_count": doc["chunk_count"],
                    "updated": "Recently",
                    "created": "—",
                    "target": "—",
                    "docs": int(doc["chunk_count"]),
                })
        else:
            # pgvector path: query documents table
            if os.environ.get("DATABASE_URL"):
                try:
                    import psycopg  # type: ignore[import-not-found]
                    from psycopg.rows import dict_row  # type: ignore[import-not-found]
                    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
                        rows = conn.execute(
                            """
                            SELECT d.id, d.title as name, d.source_type as source_kind,
                                   d.status, d.created_at, COUNT(c.id) as chunk_count
                            FROM documents d
                            LEFT JOIN document_chunks c ON c.document_id = d.id
                            WHERE d.tenant_id = %s
                            GROUP BY d.id ORDER BY d.created_at DESC LIMIT 50
                            """,
                            (tenant_id,),
                        ).fetchall()
                    for row in rows:
                        projects.append({
                            "id": str(row["id"]),
                            "code": f"PRJ-{str(row['id'])[:8].upper()}",
                            "name": row["name"],
                            "icon": "science" if row["source_kind"] == "audio" else "description",
                            "status": "Active",
                            "priority": "Medium",
                            "source_kind": row["source_kind"],
                            "chunk_count": int(row["chunk_count"]),
                            "updated": str(row.get("created_at", "—")),
                            "docs": int(row["chunk_count"]),
                        })
                except Exception as exc:
                    observability.capture_exception(exc, operation="projects.list")

        self._write_json(200, {"projects": projects, "total": len(projects)})

    def _handle_list_archives(self, claims: JwtClaims | None, workspace_id: str | None = None) -> None:
        """GET /archives — return completed/archived documents as archive records.

        For the in-memory path: completed documents that have been indexed are
        shown as cold-storage archives. In production this would read from a
        dedicated archives table with retention metadata. When ``workspace_id``
        is set, only that workspace's documents are shown.
        """
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        ws_filter = self._saas.documents_in_workspace(workspace_id) if workspace_id else None
        archives: list[dict[str, object]] = []

        if self._vector_store is not None:
            # All indexed documents are eligible to be shown as archives
            seen: dict[str, dict[str, object]] = {}
            for chunk in self._vector_store.for_tenant(tenant_id):
                if ws_filter is not None and chunk.document_id not in ws_filter:
                    continue
                if chunk.document_id not in seen:
                    seen[chunk.document_id] = {
                        "id": chunk.document_id,
                        "name": f"ARC-{chunk.document_id[:8].upper()}",
                        "title": chunk.title,
                        "kind": f"{chunk.source_type.upper()} • AES-256",
                        "classification": "Internal",
                        "archived": "—",
                        "accessed": "Recently",
                        "expiry": "7 years from creation",
                        "size": "—",
                        "status": "indexed",
                    }
            archives = list(seen.values())
        else:
            if os.environ.get("DATABASE_URL"):
                try:
                    import psycopg  # type: ignore[import-not-found]
                    from psycopg.rows import dict_row  # type: ignore[import-not-found]
                    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
                        rows = conn.execute(
                            """
                            SELECT id, title, source_type, status, created_at
                            FROM documents
                            WHERE tenant_id = %s AND status = 'ready'
                            ORDER BY created_at DESC LIMIT 100
                            """,
                            (tenant_id,),
                        ).fetchall()
                    for row in rows:
                        archives.append({
                            "id": str(row["id"]),
                            "name": f"ARC-{str(row['id'])[:8].upper()}",
                            "title": row["title"],
                            "kind": f"{row['source_type'].upper()} • AES-256",
                            "classification": "Internal",
                            "archived": str(row.get("created_at", "—"))[:10],
                            "accessed": "—",
                            "expiry": "7 years",
                            "size": "—",
                            "status": row["status"],
                        })
                except Exception as exc:
                    observability.capture_exception(exc, operation="archives.list")

        self._write_json(200, {"archives": archives, "total": len(archives)})

    def _handle_list_documents(self, claims: JwtClaims | None, workspace_id: str | None = None) -> None:
        """GET /documents — list all ingested documents for the caller's tenant.

        When ``workspace_id`` is provided, only documents tagged to that
        workspace are returned (real workspace scoping).
        """
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        ws_filter = self._saas.documents_in_workspace(workspace_id) if workspace_id else None
        docs: list[dict[str, object]] = []

        if self._vector_store is not None:
            # In-memory path: derive unique documents from the chunk store
            seen: dict[str, dict[str, object]] = {}
            for chunk in self._vector_store.for_tenant(tenant_id):
                if ws_filter is not None and chunk.document_id not in ws_filter:
                    continue
                if chunk.document_id not in seen:
                    seen[chunk.document_id] = {
                        "document_id": chunk.document_id,
                        "title": chunk.title,
                        "source_kind": chunk.source_type,
                        "chunk_count": 0,
                        "status": "ready",
                    }
                seen[chunk.document_id]["chunk_count"] = (
                    int(seen[chunk.document_id]["chunk_count"]) + 1
                )
            docs = list(seen.values())
        else:
            # pgvector path: query the documents table
            if self._connection_pool is not None or os.environ.get("DATABASE_URL"):
                try:
                    import psycopg  # type: ignore[import-not-found]
                    from psycopg.rows import dict_row  # type: ignore[import-not-found]
                    db_url = os.environ.get("DATABASE_URL")
                    if db_url:
                        with psycopg.connect(db_url, row_factory=dict_row) as conn:
                            rows = conn.execute(
                                """
                                SELECT d.id as document_id, d.title,
                                       d.source_type as source_kind,
                                       d.status,
                                       COUNT(c.id) as chunk_count
                                FROM documents d
                                LEFT JOIN document_chunks c ON c.document_id = d.id
                                WHERE d.tenant_id = %s
                                GROUP BY d.id, d.title, d.source_type, d.status
                                ORDER BY d.created_at DESC
                                LIMIT 100
                                """,
                                (tenant_id,),
                            ).fetchall()
                        docs = [dict(r) for r in rows]
                except Exception as exc:
                    observability.capture_exception(exc, operation="documents.list")
                    # Fall through and return empty list

        self._write_json(200, {"documents": docs, "total": len(docs)})

    def _handle_list_entities(self, document_id: str, claims: JwtClaims | None) -> None:
        """GET /entities/:document_id — return extracted entities for a document."""
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        entities: list[dict[str, object]] = []

        try:
            records = self._entity_service.get_records(tenant_id, document_id)
            entities = [
                {"id": r.id, "label": r.label, "value": r.value, "confidence": r.confidence}
                for r in records
            ]
        except Exception as exc:
            observability.capture_exception(exc, operation="entities.list")

        self._write_json(200, {"document_id": document_id, "entities": entities})

    def _handle_upload(self, claims: JwtClaims, tool_context: ToolContext) -> None:
        """Accept a base64-encoded file, write it to a temp path, and enqueue.

        This is the browser-facing ingestion entry point. The browser cannot
        provide a server-side file path, so it sends the file content inline as
        base64. Files are bounded by ``MAX_BODY_BYTES`` (checked before read).

        Body: {"filename": str, "content_base64": str,
               "source_kind"?: "pdf"|"audio", "title"?: str}
        """
        # RBAC
        try:
            assert_endpoint_roles(self.path, claims.roles)
        except RbacError as exc:
            self._write_json(403, {"error": str(exc)})
            self._audit_sink.record_event(
                tool_context, "access:denied", "endpoint", self.path, "denied",
                {"failure_reason": str(exc), "path": self.path},
            )
            return

        # Usage metering + plan gating (monthly upload quota).
        try:
            self._saas.record_usage(
                tenant_id=claims.tenant_id, user_id=claims.user_id, metric="uploads"
            )
        except PlanLimitExceeded as exc:
            self._write_json(402, {"error": str(exc), "metric": exc.metric,
                                   "limit": exc.limit, "upgrade_required": True})
            return

        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"status": "failed", "error": "Invalid JSON body."})
            return

        filename = payload.get("filename")
        content_b64 = payload.get("content_base64")
        if not isinstance(filename, str) or not filename.strip():
            self._write_json(400, {"status": "failed", "error": "filename is required."})
            return
        if not isinstance(content_b64, str) or not content_b64:
            self._write_json(400, {"status": "failed", "error": "content_base64 is required."})
            return

        try:
            content = base64.b64decode(content_b64, validate=True)
        except Exception:
            self._write_json(400, {"status": "failed", "error": "content_base64 is not valid base64."})
            return

        suffix = Path(filename).suffix
        upload_dir = Path(tempfile.gettempdir()) / "omni_modal_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        document_id = str(uuid.uuid4())
        dest = upload_dir / f"{document_id}{suffix}"
        dest.write_bytes(content)

        ingestion_payload = {
            "tenant_id": claims.tenant_id,
            "document_id": document_id,
            "owner_id": claims.user_id,
            "file_path": str(dest),
            "title": payload.get("title") or filename,
        }
        if payload.get("source_kind"):
            ingestion_payload["source_kind"] = payload["source_kind"]

        try:
            with observability.child_span("ingestion", "document upload"):
                request = ingestion_request_from_payload(ingestion_payload)
                job = self.queue.enqueue(request)
        except IngestionContractError as exc:
            self._write_json(400, {"status": "failed", "error": str(exc)})
            return

        self._audit_sink.record_event(
            tool_context, "ingestion:enqueued", "document", document_id, job.status,
            {"job_id": job.id, "document_id": document_id, "tenant_id": claims.tenant_id,
             "file_name": filename},
        )
        # Tag the document to the active workspace (real workspace scoping).
        ws = payload.get("workspace_id")
        if isinstance(ws, str) and ws:
            self._saas.tag_document(document_id, ws)
        self._saas.analytics.capture(
            event="upload", tenant_id=claims.tenant_id, user_id=claims.user_id,
            properties={"source_kind": payload.get("source_kind", "pdf")},
        )
        self._saas.notifications.add(
            tenant_id=claims.tenant_id, title="Upload received",
            body=f"'{payload.get('title') or filename}' is being processed.",
            kind="info", user_id=claims.user_id,
        )
        self._write_json(202, {"job_id": job.id, "status": job.status, "document_id": document_id})

    def _handle_list_workspaces(self, claims: JwtClaims | None) -> None:
        """GET /workspaces — list workspaces in the caller's organization."""
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        user_id = claims.user_id if claims else "demo-user"
        org = self._saas.ensure_org(tenant_id, owner_user_id=user_id)
        workspaces = [w.to_dict() for w in self._saas.workspaces.list_workspaces(org.id)]
        self._write_json(200, {
            "organization": org.to_dict(),
            "workspaces": workspaces,
            "total": len(workspaces),
        })

    def _handle_usage(self, claims: JwtClaims | None) -> None:
        """GET /usage — current plan + monthly usage vs limits."""
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        user_id = claims.user_id if claims else "demo-user"
        self._saas.ensure_org(tenant_id, owner_user_id=user_id)
        self._write_json(200, self._saas.usage_report(tenant_id))

    def _handle_list_members(self, claims: JwtClaims | None) -> None:
        """GET /members — team members and pending invites for the org."""
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        user_id = claims.user_id if claims else "demo-user"
        org = self._saas.ensure_org(tenant_id, owner_user_id=user_id)
        members = [m.to_dict() for m in self._saas.workspaces.list_members(org.id)]
        invites = [
            i.to_dict() for i in self._saas.workspaces.list_invites(org.id)
            if i.status == "pending"
        ]
        self._write_json(200, {"members": members, "invites": invites,
                               "total": len(members)})

    def _handle_list_notifications(self, claims: JwtClaims | None) -> None:
        """GET /notifications — notifications for the caller."""
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        user_id = claims.user_id if claims else None
        notes = self._saas.notifications.list_for(tenant_id, user_id=user_id)
        self._write_json(200, {
            "notifications": [n.to_dict() for n in notes],
            "unread": self._saas.notifications.unread_count(tenant_id, user_id=user_id),
            "total": len(notes),
        })

    def _handle_list_plans(self, claims: JwtClaims | None) -> None:
        """GET /plans — available subscription plans."""
        from omni_modal.saas.plans import PLANS  # noqa: PLC0415
        self._write_json(200, {"plans": [p.to_dict() for p in PLANS.values()]})

    def _handle_billing(self, claims: JwtClaims | None) -> None:
        """GET /billing — current plan, billing mode (honest), and usage."""
        from omni_modal.saas.plans import PLANS  # noqa: PLC0415
        tenant_id = claims.tenant_id if claims else "demo-tenant"
        user_id = claims.user_id if claims else "demo-user"
        org = self._saas.ensure_org(tenant_id, owner_user_id=user_id)
        self._write_json(200, {
            "billing_mode": self._saas.billing_mode(),
            "current_plan": org.plan_id,
            "plans": [p.to_dict() for p in PLANS.values()],
            "usage": self._saas.usage_report(tenant_id),
        })

    def _handle_admin_stats(self, claims: JwtClaims | None) -> None:
        """GET /admin/stats — admin-only operational dashboard data."""
        if claims is None or "admin" not in claims.roles:
            self._write_json(403, {"error": "Endpoint /admin/stats requires the admin role."})
            return
        tenant_id = claims.tenant_id
        org = self._saas.ensure_org(tenant_id, owner_user_id=claims.user_id)
        analytics = self._saas.analytics
        event_counts = analytics.event_counts() if hasattr(analytics, "event_counts") else {}
        self._write_json(200, {
            "organization": org.to_dict(),
            "members": self._saas.workspaces.count_members(org.id),
            "workspaces": self._saas.workspaces.count_workspaces(org.id),
            "usage": self._saas.usage.snapshot(tenant_id),
            "event_counts": event_counts,
            "adapters": {
                "storage": self._saas.storage.backend,
                "email": self._saas.email.backend,
                "analytics": self._saas.analytics.backend,
            },
            "audit_events": len(self._audit_sink.entries),
            "billing_mode": self._saas.billing_mode(),
        })

    def _handle_create_workspace(self, claims: JwtClaims) -> None:
        """POST /workspaces — create a workspace (plan-limited)."""
        try:
            assert_endpoint_roles("/workspaces", claims.roles)
        except RbacError as exc:
            self._write_json(403, {"error": str(exc)})
            return
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            self._write_json(400, {"error": "name is required."})
            return
        try:
            ws = self._saas.create_workspace(
                tenant_id=claims.tenant_id, user_id=claims.user_id, name=name.strip()
            )
        except PlanLimitExceeded as exc:
            self._write_json(402, {"error": str(exc), "metric": exc.metric,
                                   "limit": exc.limit, "upgrade_required": True})
            return
        self._write_json(201, ws.to_dict())

    def _handle_create_invite(self, claims: JwtClaims) -> None:
        """POST /invites — invite a team member (admin only, plan-limited)."""
        try:
            assert_endpoint_roles("/invites", claims.roles)
        except RbacError as exc:
            self._write_json(403, {"error": str(exc)})
            return
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        email = payload.get("email")
        role = payload.get("role", "researcher")
        if not isinstance(email, str) or "@" not in email:
            self._write_json(400, {"error": "A valid email is required."})
            return
        if role not in ("researcher", "admin", "auditor"):
            self._write_json(400, {"error": "role must be researcher, admin, or auditor."})
            return
        try:
            invite = self._saas.invite_member(
                tenant_id=claims.tenant_id, user_id=claims.user_id, email=email, role=role
            )
        except PlanLimitExceeded as exc:
            self._write_json(402, {"error": str(exc), "metric": exc.metric,
                                   "limit": exc.limit, "upgrade_required": True})
            return
        # Return a shareable accept link (token included) so the admin can copy
        # it directly — useful in console-email/demo mode where the email isn't
        # actually delivered. This mirrors how real SaaS tools surface invite links.
        body = invite.to_dict(include_token=True)
        body["accept_url"] = f"/accept-invite?token={invite.token}"
        self._write_json(201, body)

    def _handle_change_plan(self, claims: JwtClaims) -> None:
        """POST /billing/change-plan — change subscription plan (admin only, demo)."""
        try:
            assert_endpoint_roles("/billing/change-plan", claims.roles)
        except RbacError as exc:
            self._write_json(403, {"error": str(exc)})
            return
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        plan_id = payload.get("plan_id")
        org = self._saas.change_plan(
            tenant_id=claims.tenant_id, user_id=claims.user_id, plan_id=str(plan_id)
        )
        if org is None:
            self._write_json(400, {"error": f"Unknown plan_id: {plan_id!r}."})
            return
        self._write_json(200, {
            "organization": org.to_dict(),
            "billing_mode": self._saas.billing_mode(),
        })

    def _billing_urls(self) -> tuple[str, str]:
        """Return (success_base, cancel_url) for Stripe redirects.

        Uses APP_BASE_URL (frontend origin) so Stripe returns the user to the
        billing page. Falls back to localhost:3000 for local dev.
        """
        app_base = os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")
        success_url = f"{app_base}/billing?status=success&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{app_base}/billing?status=cancelled"
        return success_url, cancel_url

    def _handle_billing_checkout(self, claims: JwtClaims) -> None:
        """POST /billing/checkout — start a Stripe Checkout for a paid plan."""
        try:
            assert_endpoint_roles("/billing/checkout", claims.roles)
        except RbacError as exc:
            self._write_json(403, {"error": str(exc)})
            return
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        plan_id = str(payload.get("plan_id", ""))
        success_url, cancel_url = self._billing_urls()
        try:
            result = self._saas.start_checkout(
                tenant_id=claims.tenant_id, user_id=claims.user_id, plan_id=plan_id,
                success_url=success_url, cancel_url=cancel_url,
            )
        except RuntimeError as exc:
            # Demo billing active — no Stripe checkout available.
            self._write_json(409, {"error": str(exc), "billing_mode": self._saas.billing_mode()})
            return
        except Exception as exc:  # Stripe API error
            observability.capture_exception(exc, operation="billing_checkout")
            self._write_json(502, {"error": f"Stripe checkout failed: {exc}"})
            return
        if result is None:
            self._write_json(400, {"error": f"Plan {plan_id!r} is not purchasable."})
            return
        self._write_json(200, result)

    def _handle_billing_confirm(self, claims: JwtClaims) -> None:
        """POST /billing/confirm — verify a returned Checkout session, apply plan."""
        try:
            assert_endpoint_roles("/billing/confirm", claims.roles)
        except RbacError as exc:
            self._write_json(403, {"error": str(exc)})
            return
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        session_id = str(payload.get("session_id", ""))
        if not session_id:
            self._write_json(400, {"error": "session_id is required."})
            return
        try:
            result = self._saas.confirm_checkout(
                tenant_id=claims.tenant_id, user_id=claims.user_id, session_id=session_id,
            )
        except RuntimeError as exc:
            self._write_json(409, {"error": str(exc)})
            return
        except Exception as exc:
            observability.capture_exception(exc, operation="billing_confirm")
            self._write_json(502, {"error": f"Stripe confirm failed: {exc}"})
            return
        self._write_json(200, result or {"paid": False})

    def _handle_billing_portal(self, claims: JwtClaims) -> None:
        """POST /billing/portal — open the Stripe Billing Portal."""
        try:
            assert_endpoint_roles("/billing/portal", claims.roles)
        except RbacError as exc:
            self._write_json(403, {"error": str(exc)})
            return
        app_base = os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")
        try:
            result = self._saas.start_portal(
                tenant_id=claims.tenant_id, user_id=claims.user_id,
                return_url=f"{app_base}/billing",
            )
        except RuntimeError as exc:
            self._write_json(409, {"error": str(exc), "billing_mode": self._saas.billing_mode()})
            return
        except Exception as exc:
            observability.capture_exception(exc, operation="billing_portal")
            self._write_json(502, {"error": f"Stripe portal failed: {exc}"})
            return
        if result is None:
            self._write_json(409, {"error": "No Stripe customer yet. Subscribe to a paid plan first."})
            return
        self._write_json(200, result)

    def _issue_login_token(self, account) -> dict:
        """Issue an access + rotating refresh token pair for an account."""
        return self._session_service.issue(account)

    def _handle_auth_refresh(self) -> None:
        """POST /auth/refresh — rotate a refresh token, return a new pair."""
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        refresh_token = str(payload.get("refresh_token", ""))
        try:
            result = self._session_service.refresh(refresh_token)
        except RefreshTokenError as exc:
            self._write_json(401, {"error": str(exc)})
            return
        except Exception as exc:
            observability.capture_exception(exc, operation="auth.refresh")
            self._write_json(500, {"error": "Token refresh failed."})
            return
        self._write_json(200, result)

    def _handle_auth_logout(self) -> None:
        """POST /auth/logout — revoke a refresh token (idempotent)."""
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        refresh_token = str(payload.get("refresh_token", ""))
        try:
            revoked = self._session_service.revoke(refresh_token)
        except Exception as exc:
            observability.capture_exception(exc, operation="auth.logout")
            self._write_json(500, {"error": "Logout failed."})
            return
        self._write_json(200, {"revoked": revoked})

    def _handle_auth_register(self) -> None:
        """POST /auth/register — create an account, return a signed JWT."""
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        email = str(payload.get("email", ""))
        password = str(payload.get("password", ""))
        display_name = payload.get("display_name")
        try:
            account = self._account_service.register(
                email=email, password=password,
                display_name=display_name if isinstance(display_name, str) else None,
            )
        except AccountError as exc:
            self._write_json(409, {"error": str(exc)})
            return
        except Exception as exc:
            observability.capture_exception(exc, operation="auth.register")
            self._write_json(500, {"error": "Registration failed."})
            return
        self._write_json(201, self._issue_login_token(account))

    def _handle_auth_login(self) -> None:
        """POST /auth/login — verify credentials, return a signed JWT."""
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        email = str(payload.get("email", ""))
        password = str(payload.get("password", ""))
        try:
            account = self._account_service.authenticate(email=email, password=password)
        except Exception as exc:
            observability.capture_exception(exc, operation="auth.login")
            self._write_json(500, {"error": "Login failed."})
            return
        if account is None:
            self._write_json(401, {"error": "Invalid email or password."})
            return
        self._write_json(200, self._issue_login_token(account))

    def _handle_stripe_webhook(self) -> None:
        """POST /billing/webhook — verified Stripe events (unauthenticated)."""
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(content_length) if content_length else b""
        sig_header = self.headers.get("Stripe-Signature", "")
        billing = self._saas.billing
        if not getattr(billing, "supports_checkout", False):
            self._write_json(200, {"received": True, "note": "demo billing — ignored"})
            return
        try:
            event = billing.verify_webhook(payload, sig_header)
        except Exception as exc:
            observability.capture_exception(exc, operation="billing_webhook_verify")
            self._write_json(400, {"error": f"Webhook signature verification failed: {exc}"})
            return
        try:
            self._saas.apply_webhook_event(event)
        except Exception as exc:
            observability.capture_exception(exc, operation="billing_webhook_apply")
            self._write_json(500, {"error": "Webhook processing error."})
            return
        self._write_json(200, {"received": True})
        """GET /invites/preview?token= — redacted invite info for the accept page."""
        if not token:
            self._write_json(400, {"error": "token query parameter is required."})
            return
        preview = self._saas.preview_invite(token)
        if preview is None:
            self._write_json(404, {"error": "Invite not found."})
            return
        self._write_json(200, preview)

    def _handle_accept_invite(self, claims: JwtClaims) -> None:
        """POST /invites/accept — accept an invite token as the signed-in user."""
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            self._write_json(400, {"error": "token is required."})
            return
        member = self._saas.accept_invite(token=token, user_id=claims.user_id)
        if member is None:
            self._write_json(410, {"error": "Invite is invalid, expired, or already used."})
            return
        self._write_json(200, member.to_dict())

    def _handle_mark_notifications_read(self, claims: JwtClaims) -> None:
        """POST /notifications/read — mark one or all notifications read."""
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(400, {"error": "Invalid JSON body."})
            return
        tenant_id = claims.tenant_id
        if payload.get("all"):
            count = self._saas.notifications.mark_all_read(tenant_id, user_id=claims.user_id)
            self._write_json(200, {"marked_read": count})
            return
        note_id = payload.get("id")
        if not isinstance(note_id, str):
            self._write_json(400, {"error": "id is required (or pass all=true)."})
            return
        ok = self._saas.notifications.mark_read(tenant_id, note_id)
        self._write_json(200 if ok else 404, {"marked_read": 1 if ok else 0})

    def _handle_query(self, stream: bool, claims: JwtClaims | None = None) -> None:
        tool_context: ToolContext | None = None
        if claims:
            tool_context = ToolContext(
                tenant_id=claims.tenant_id,
                actor_user_id=claims.user_id,
                roles=claims.roles,
            )

        # RBAC check
        if claims:
            try:
                assert_endpoint_roles(self.path, claims.roles)
            except RbacError as exc:
                self._write_json(403, {"error": str(exc)})
                if tool_context:
                    self._audit_sink.record_event(
                        tool_context, "access:denied", "endpoint", self.path, "denied",
                        {"failure_reason": str(exc), "path": self.path}
                    )
                return

        # Usage metering + plan gating (monthly query quota).
        if claims:
            try:
                self._saas.record_usage(
                    tenant_id=claims.tenant_id, user_id=claims.user_id, metric="queries"
                )
            except PlanLimitExceeded as exc:
                self._write_json(402, {"error": str(exc), "metric": exc.metric,
                                       "limit": exc.limit, "upgrade_required": True})
                return

        try:
            payload = self._read_json_body()

            # Input validation on query fields
            if "question" in payload or "query" in payload:
                query_text = str(payload.get("question") or payload.get("query", ""))
                try:
                    assert_query_length(query_text)
                except ValidationError as exc:
                    self._write_json(400, {"error": str(exc)})
                    return

            # tenant_id and user_id are authoritative from the verified JWT —
            # never trust client-supplied values (prevents cross-tenant access).
            request_payload: dict[str, object] = {**payload, "stream": stream}
            if claims:
                request_payload["tenant_id"] = claims.tenant_id
                request_payload["user_id"] = claims.user_id
            request = query_request_from_payload(request_payload)

            query_hash = hashlib.sha256(request.question.encode()).hexdigest()[:16]

            with observability.child_span("retrieval", "vector retrieval"):
                result = self.research_workflow.answer(request)

            # Task 11.8: Audit query completion
            if tool_context:
                self._audit_sink.record_event(
                    tool_context, "query:complete", "endpoint", self.path, "ok",
                    {"query_hash": query_hash, "tenant_id": claims.tenant_id if claims else None}
                )
            if claims:
                self._saas.analytics.capture(
                    event="query", tenant_id=claims.tenant_id, user_id=claims.user_id
                )

            response = result.response
            if stream:
                self._write_sse_response(
                    response.answer_markdown,
                    {**response.to_json_dict(), "trace": result.trace},
                )
            else:
                self._write_json(200, {**response.to_json_dict(), "trace": result.trace})
        except QueryContractError as exc:
            self._write_json(400, {"status": "failed", "error": str(exc)})
        except json.JSONDecodeError:
            self._write_json(400, {"status": "failed", "error": "Invalid JSON body."})

    def _read_json_body(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise json.JSONDecodeError("empty body", "", 0)
        if content_length > MAX_BODY_BYTES:
            raise IngestionContractError("Request body is too large.")

        body = self.rfile.read(content_length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise IngestionContractError("Request body must be a JSON object.")
        return payload

    def _write_json(self, status_code: int, data: dict[str, object]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _add_cors_headers(self) -> None:
        """Add CORS headers so the browser can call the API from a different origin."""
        origin = self.headers.get("Origin", "")
        allowed_origins = {
            os.environ.get("NEXT_PUBLIC_BACKEND_URL", ""),
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
        }
        # Allow the requesting origin if it's in the allowed set, else default
        cors_origin = origin if origin in allowed_origins else "http://localhost:3000"
        self.send_header("Access-Control-Allow-Origin", cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, Accept, X-Correlation-ID, sentry-trace, baggage",
        )
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests from the browser."""
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    def _write_sse_response(
        self, markdown: str, final_payload: dict[str, object]
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self._add_cors_headers()
        self.end_headers()

        for chunk in stream_markdown(markdown):
            event = json.dumps({"delta": chunk})
            self.wfile.write(f"event: delta\ndata: {event}\n\n".encode("utf-8"))
            self.wfile.flush()

        done = json.dumps(final_payload)
        self.wfile.write(f"event: done\ndata: {done}\n\n".encode("utf-8"))
        self.wfile.flush()


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    OmniModalHandler.queue.start_worker()
    atexit.register(close_connection_pool)
    _host = os.environ.get("HOST", host)
    _port = int(os.environ.get("PORT", str(port)))
    server = HTTPServer((_host, _port), OmniModalHandler)
    server.serve_forever()


if __name__ == "__main__":
    run()
