"""FastAPI app: async endpoints, Pydantic validation, OpenAPI, reusing the
existing domain services so behaviour matches the stdlib server exactly."""

from __future__ import annotations

import base64
import hashlib
import tempfile
import threading
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from omni_modal.entity_extraction.service import EntityExtractionService
from omni_modal.ingestion import MultimodalIngestionPipeline
from omni_modal.documents.dispatch import InProcessDispatcher
from omni_modal.documents.service import DocumentService, Forbidden, InvalidState, UploadRejected
from omni_modal.documents.store import DocumentNotFound, DocumentQuery, select_document_store
from omni_modal.entity_extraction.records import select_entity_store
from omni_modal.projects.service import ProjectRejected, ProjectService
from omni_modal.projects.store import ProjectNotFound, select_project_store
from omni_modal.ingestion.groq_transcriber import select_audio_transcriber
from omni_modal.observability import observability
from omni_modal.orchestration import InternalResearchAdkWorkflow, Phase1Orchestrator
from omni_modal.orchestration.adk_workflow import AgentWorkflowResult
from omni_modal.orchestration.a2a import external_client_from_environment
from omni_modal.research.external import select_web_search
from omni_modal.qa import (
    PgVectorChunkRetriever,
    QueryRequest,
    select_answer_synthesizer,
    select_embedding_provider,
)
from omni_modal.qa.llm import select_chat_client
from omni_modal.research.assistant import Assistant, AssistantContext
from omni_modal.research.followups import QueryRewriter
from omni_modal.research.sessions import SessionNotFound, select_research_session_service
from omni_modal.qa.in_memory_store import (
    InMemoryChunkPersistence,
    InMemoryChunkRetriever,
    InMemoryVectorStore,
)
from omni_modal.saas import get_saas_service
from omni_modal.saas.adapters.storage import content_disposition, safe_download_name
from omni_modal.saas.plans import FEATURE_EXTERNAL_DELEGATION, PLANS, PlanLimitExceeded, get_plan
from omni_modal.security.accounts import AccountError, get_account_service
from omni_modal.security.auth import AuthError, JwtClaims, _make_jwt, jwt_secret_from_env, verify_jwt
from omni_modal.security.mfa import MfaAlreadyEnrolled, MfaLocked
from omni_modal.security.rbac import RbacError, assert_endpoint_roles
from omni_modal.security.rate_limiting import RateLimitExceeded, general_limits, heavy_limits
from omni_modal.security.redis_rate_limiter import select_rate_limiter
from omni_modal.qa.redis_cache import select_query_cache


# ── Pydantic schemas ─────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=8, max_length=1024)
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    tenant_id: str
    user_id: str
    roles: list[str]
    email: str
    expires_at: int
    access_expires_at: int | None = None
    refresh_token: str | None = None
    refresh_expires_at: int | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class MfaConfirmRequest(BaseModel):
    code: str


class MfaLoginRequest(BaseModel):
    mfa_token: str
    code: str


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., max_length=320)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., max_length=512)
    password: str = Field(..., max_length=1024)


INVALID_RESET_LINK = "This reset link is invalid or has expired."


class MfaEnrollRequest(BaseModel):
    # Turning on two-step sign-in re-checks the password, so a stolen access
    # token alone cannot attach an attacker's authenticator to the account.
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=1024)


# Wrong codes allowed per MFA login challenge before the password step must
# be repeated. (The second factor itself also locks after repeated wrong
# codes across challenges: see omni_modal.security.mfa.)
MFA_MAX_ATTEMPTS = 5
# How long after the password step a login challenge stays usable.
MFA_CHALLENGE_SECONDS = 300


class QueryRequestModel(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(5, ge=1, le=50)
    min_similarity: float = Field(0.0, ge=0.0, le=1.0)
    # Continue a saved session; a new one is created when absent (stream only).
    session_id: str | None = Field(None, max_length=64)
    # For a new session: limit research to this project's documents.
    project_id: str | None = Field(None, max_length=64)


class ProjectRequest(BaseModel):
    name: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=5000)


class ProjectDocumentRequest(BaseModel):
    document_id: str = Field(..., max_length=64)


class ProjectItemRequest(BaseModel):
    kind: str = Field(..., max_length=20)
    title: str = Field(..., max_length=1000)
    body: str | None = Field(None, max_length=50_000)
    citations: list[dict] | None = None
    source_session_id: str | None = Field(None, max_length=64)


class ProjectItemUpdate(BaseModel):
    title: str | None = Field(None, max_length=1000)
    body: str | None = Field(None, max_length=50_000)


class ResearchSettingsRequest(BaseModel):
    external_research_enabled: bool


class SessionProjectRequest(BaseModel):
    project_id: str | None = Field(None, max_length=64)


class CreateSessionRequest(BaseModel):
    title: str | None = Field(None, max_length=500)
    kind: str = Field("research", pattern="^(research|assistant)$")


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)


class AssistantRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(None, max_length=64)
    # The path of the page the user is on, e.g. "/documents".
    page: str | None = Field(None, max_length=200)


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class InviteRequest(BaseModel):
    email: str = Field(..., max_length=320)
    role: str = Field(..., max_length=80)


class AcceptInviteRequest(BaseModel):
    token: str


class ChangePlanRequest(BaseModel):
    plan_id: str


class CheckoutRequest(BaseModel):
    plan_id: str


class ConfirmRequest(BaseModel):
    session_id: str


class NotificationsReadRequest(BaseModel):
    id: str | None = None
    all: bool = False


class UploadRequest(BaseModel):
    filename: str = Field(..., max_length=300)
    # Base64 of at most the 50 MB upload limit; anything longer is refused unread.
    content_base64: str = Field(..., max_length=69905068)
    source_kind: str | None = None
    title: str | None = Field(None, max_length=300)
    workspace_id: str | None = None


class CreateUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=300)
    size_bytes: int = Field(..., ge=1)
    content_type: str = Field("application/octet-stream", max_length=200)
    sha256: str = Field(..., min_length=64, max_length=64)
    title: str | None = Field(None, max_length=300)
    workspace_id: str | None = None


# ── App state / lifespan ─────────────────────────────────────────────────
class _State:
    workflow: Any = None
    saas: Any = None
    accounts: Any = None
    queue: Any = None
    entity_service: Any = None
    vector_store: Any = None
    audit_sink: Any = None
    rate_limiter: Any = None
    heavy_rate_limiter: Any = None
    web_search: Any = None
    query_cache: Any = None
    sessions: Any = None
    mfa: Any = None
    password_resets: Any = None
    retriever: Any = None
    documents: Any = None
    projects: Any = None
    dispatcher: Any = None
    research_sessions: Any = None
    rewriter: Any = None
    assistant: Any = None


state = _State()


def _build_runtime() -> None:
    """Construct the data-plane components, mirroring the stdlib server so both
    share identical behaviour. Postgres path when DATABASE_URL is set, else the
    in-memory demo path."""
    if os.environ.get("DATABASE_URL"):
        # Fail with a plain message, not a missing-column error deep in startup.
        from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415
        from omni_modal.db.schema_check import assert_schema_current  # noqa: PLC0415

        with get_connection_pool().connection() as conn:
            assert_schema_current(conn)
    selection = select_embedding_provider()
    provider = selection.provider
    # Local Whisper when installed, else Groq's hosted Whisper (the deployment
    # cannot run a local model), else a clear "Whisper is not installed" error.
    audio = select_audio_transcriber()
    state.entity_service = EntityExtractionService(store=select_entity_store())

    # Shared coordination primitives — Redis-backed when REDIS_URL is set
    # (stateless, horizontally-scalable web tier), else in-process.
    state.query_cache = select_query_cache()
    state.rate_limiter = select_rate_limiter(general_limits())
    state.heavy_rate_limiter = select_rate_limiter(heavy_limits(), namespace="rlh")

    if os.environ.get("DATABASE_URL"):
        from omni_modal.qa.pg_persistence import PostgresChunkPersistence  # noqa: PLC0415

        state.vector_store = None
        retriever = PgVectorChunkRetriever(provider, cache=state.query_cache, model_id=selection.model_id)
        persistence = PostgresChunkPersistence(
            provider, database_url=os.environ.get("DATABASE_URL"),
            embedding_model=selection.model_id,
            dimensions=getattr(provider, "dimensions", 384),
            require_document=True,
        )
        pipeline = MultimodalIngestionPipeline(audio_transcriber=audio, persistence=persistence)
        # Chunks of a deleted document go by cascade; a failed one is cleared explicitly.
        clear_index = persistence.delete_document_chunks
    else:
        state.vector_store = InMemoryVectorStore()
        retriever = InMemoryChunkRetriever(provider, state.vector_store)
        persistence = InMemoryChunkPersistence(state.vector_store, provider)
        pipeline = MultimodalIngestionPipeline(audio_transcriber=audio, persistence=persistence)
        clear_index = state.vector_store.remove_document

    state.saas = get_saas_service()
    state.documents = DocumentService(
        store=select_document_store(),
        storage=state.saas.storage,
        pipeline=pipeline,
        remove_vectors=clear_index,
        entity_service=state.entity_service,
        evict_cache=state.query_cache.evict_tenant,
        notify=_notify_processing,
    )
    if state.dispatcher is not None:
        state.dispatcher.stop(timeout=1.0)
    if isinstance(persistence, InMemoryChunkPersistence):
        persistence.document_exists = _document_exists
    state.dispatcher = InProcessDispatcher(state.documents.run_job, sweep=state.documents.sweep)
    state.documents.set_dispatch(state.dispatcher, state.dispatcher.later)
    state.dispatcher.start()
    try:
        state.documents.resume_unfinished()
    except Exception as exc:  # noqa: BLE001 - a resume problem must not stop the API starting
        observability.capture_exception(exc, operation="documents.resume_unfinished")
    state.retriever = retriever
    if isinstance(retriever, InMemoryChunkRetriever):
        retriever.is_searchable = _document_searchable
    state.web_search = select_web_search()
    state.workflow = InternalResearchAdkWorkflow(
        retriever, external_client_from_environment(), select_answer_synthesizer(),
        web_search=state.web_search,
    )
    chat_client = select_chat_client()
    state.research_sessions = select_research_session_service()
    state.projects = ProjectService(
        store=select_project_store(), documents=state.documents,
        sessions=state.research_sessions, entities=state.entity_service,
    )
    state.rewriter = QueryRewriter(chat_client)
    state.assistant = Assistant(
        retriever=retriever,
        documents_fn=lambda tenant_id: _all_docs(tenant_id, None),
        saas=_SaasReader(state.saas),
        chat_client=chat_client,
    )
    state.accounts = get_account_service()
    from omni_modal.security.sessions import select_session_service  # noqa: PLC0415

    state.sessions = select_session_service()
    from omni_modal.security.mfa import select_mfa_service  # noqa: PLC0415

    state.mfa = select_mfa_service()
    from omni_modal.security.password_reset import select_password_reset_service  # noqa: PLC0415

    state.password_resets = select_password_reset_service()
    from omni_modal.security.pg_audit_sink import select_audit_sink  # noqa: PLC0415

    state.audit_sink = select_audit_sink()


_MODEL_STATUS_TTL = 300.0
_model_status_cache: dict[str, object] = {"at": 0.0, "value": ""}


_model_status_lock = threading.Lock()


def _embedding_status() -> str:
    """Which model makes search vectors; never loads it (it loads on first use)."""
    provider = getattr(state.retriever, "_embedding_provider", None) or getattr(state.retriever, "_provider", None)
    model_id = str(getattr(provider, "model_id", None) or type(provider).__name__)
    error = getattr(provider, "last_error", None)
    if error:
        return f"{model_id}: unavailable, search uses keywords only"
    loaded = getattr(provider, "loaded", None)
    return f"{model_id} ({'loaded' if loaded else 'loads on first use'})" if loaded is not None else model_id


def _refresh_model_status() -> None:
    import urllib.error  # noqa: PLC0415

    client = select_chat_client()
    try:
        client.ping()
        value = f"ok ({client.name})"
    except urllib.error.HTTPError as exc:
        # 401/403: the key; 404: the model name. No response body, so nothing secret.
        value = f"{client.name} answered HTTP {exc.code} (answers fall back to extracts)"
    except LookupError:
        value = f"{client.name} key works but the model name is not available (answers fall back to extracts)"
    except Exception:  # noqa: BLE001
        value = "unreachable (answers fall back to extracts)"
    _model_status_cache.update(at=time.time(), value=value, refreshing=False)


def _model_provider_status() -> str:
    """The model provider's last known state. The probe itself never waits on the
    provider: a stale value is refreshed by one background thread at most every 5 minutes."""
    if select_chat_client() is None:
        return "not configured (answers use extracts)"
    with _model_status_lock:
        stale = time.time() - float(_model_status_cache["at"]) >= _MODEL_STATUS_TTL
        if stale and not _model_status_cache.get("refreshing"):
            _model_status_cache["refreshing"] = True
            threading.Thread(target=_refresh_model_status, daemon=True, name="ModelStatus").start()
    return str(_model_status_cache["value"] or "checking")


def _document_searchable(tenant_id: str, document_id: str) -> bool:
    """In the demo store: archived documents are left out of research.
    Documents loaded straight into the vector store (scripts, tests) have no record and stay searchable."""
    try:
        doc, _ = state.documents.get(tenant_id, document_id)
    except DocumentNotFound:
        return True
    return doc.archived_at is None


def _document_exists(tenant_id: str, document_id: str) -> bool:
    try:
        state.documents.get(tenant_id, document_id)
        return True
    except DocumentNotFound:
        return False


def _notify_processing(doc, outcome: str) -> None:
    """Tell the uploader when their document is ready or has failed."""
    try:
        if outcome == "ready":
            state.saas.notifications.add(
                tenant_id=doc.tenant_id, title="Document ready",
                body=f"'{doc.title}' has been processed and can be searched.", kind="success",
                user_id=doc.uploaded_by,
            )
        else:
            state.saas.notifications.add(
                tenant_id=doc.tenant_id, title="Processing failed",
                body=f"'{doc.title}' could not be processed. Open Documents for the reason.", kind="error",
                user_id=doc.uploaded_by,
            )
    except Exception as exc:  # noqa: BLE001 - a notification must never fail processing
        observability.capture_exception(exc, operation="documents.notify")


class _SaasReader:
    """The read-only slice of the SaaS service the assistant may use."""

    def __init__(self, saas) -> None:
        self._saas = saas

    def usage_report(self, tenant_id: str) -> dict:
        return self._saas.usage_report(tenant_id)

    def list_members(self, tenant_id: str, user_id: str) -> list[dict]:
        org = self._saas.ensure_org(tenant_id, owner_user_id=user_id)
        return [m.to_dict() for m in self._saas.workspaces.list_members(org.id)]


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


# Proxies must pass stream chunks through as they arrive.
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
# Earlier question/answer pairs passed to follow-ups.
HISTORY_TURNS = 4


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _build_runtime()
    yield
    observability.flush()


def create_app() -> FastAPI:
    app = FastAPI(
        title="OMERO API",
        version="1.0.0",
        description="Omni-Modal Enterprise Research Orchestrator — async API surface.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("CORS_ALLOW_ORIGINS", "*").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from omni_modal.api.middleware import install_observability, install_security_headers  # noqa: PLC0415

    install_observability(app)
    install_security_headers(app)

    # ── Auth dependency ──────────────────────────────────────────────────
    def _limit(limiter, claims: JwtClaims) -> None:
        if limiter is None:
            return
        try:
            limiter.check_tenant(claims.tenant_id)
            limiter.check_user(claims.tenant_id, claims.user_id)
        except RateLimitExceeded as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Retry after {exc.retry_after}s.",
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc

    def current_claims(authorization: str | None = Header(default=None)) -> JwtClaims:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
        token = authorization.split(" ", 1)[1].strip()
        try:
            claims = verify_jwt(token, jwt_secret_from_env())
        except AuthError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

        # Distributed (or in-process) rate limiting on every authenticated
        # request; costly routes also pass the stricter limit (heavy_request).
        _limit(state.rate_limiter, claims)
        return claims

    def heavy_request(claims: JwtClaims = Depends(current_claims)) -> JwtClaims:
        _limit(state.heavy_rate_limiter, claims)
        return claims

    def require_roles(path: str):
        def _dep(claims: JwtClaims = Depends(current_claims)) -> JwtClaims:
            try:
                assert_endpoint_roles(path, claims.roles)
            except RbacError as exc:
                raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
            return claims
        return _dep

    def meter(claims: JwtClaims, metric: str) -> None:
        try:
            state.saas.record_usage(tenant_id=claims.tenant_id, user_id=claims.user_id, metric=metric)
        except PlanLimitExceeded as exc:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                detail={"error": str(exc), "metric": exc.metric, "limit": exc.limit, "upgrade_required": True},
            ) from exc

    # ── Health ───────────────────────────────────────────────────────────
    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return Phase1Orchestrator().health()

    @app.get("/health/ready", tags=["system"])
    async def readiness():
        """Readiness probe: verifies configured dependencies are reachable.

        Liveness (`/health`) only says the process is up; readiness says it can
        actually serve traffic. Returns 503 when a *configured* dependency
        (Postgres, Redis) is unreachable so orchestrators can route around it.
        """
        from fastapi.responses import JSONResponse  # noqa: PLC0415

        checks: dict[str, str] = {}
        ok = True

        if os.environ.get("DATABASE_URL"):
            try:
                from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

                pool = get_connection_pool()
                with pool.connection() as conn, conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
                checks["database"] = "ok"
            except Exception as exc:  # noqa: BLE001
                checks["database"] = f"error: {type(exc).__name__}"
                ok = False
        else:
            checks["database"] = "in-memory (not configured)"

        if os.environ.get("REDIS_URL"):
            try:
                from omni_modal.cache.redis_client import get_redis_client  # noqa: PLC0415

                client = get_redis_client()
                if client is not None and client.ping():
                    checks["redis"] = "ok"
                else:
                    checks["redis"] = "unreachable"
                    ok = False
            except Exception as exc:  # noqa: BLE001
                checks["redis"] = f"error: {type(exc).__name__}"
                ok = False
        else:
            checks["redis"] = "in-process (not configured)"

        storage = getattr(state.saas, "storage", None) if state.saas is not None else None
        if storage is not None:
            try:
                await run_in_threadpool(storage.head, "readiness-probe/does-not-exist")
                checks["storage"] = f"ok ({storage.backend})"
            except Exception as exc:  # noqa: BLE001
                # S3's error code (e.g. 403 AccessDenied, 301 PermanentRedirect) says what to fix.
                code = str(((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", "")).strip()
                checks["storage"] = f"error: {type(exc).__name__}" + (f" {code}" if code else "")
                ok = False

        # The model provider is optional (answers fall back to extracts without it),
        # so its state is reported but never makes the service "not ready".
        checks["model_provider"] = _model_provider_status()
        checks["embeddings"] = _embedding_status()

        body = {"status": "ready" if ok else "not_ready", "checks": checks}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/metrics", tags=["system"])
    async def prometheus_metrics():
        """Prometheus text exposition of request counters + latency histograms."""
        from fastapi.responses import PlainTextResponse  # noqa: PLC0415

        from omni_modal.metrics import metrics  # noqa: PLC0415

        return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

    # ── Auth ─────────────────────────────────────────────────────────────
    def _issue(account) -> AuthResponse:
        return AuthResponse(**state.sessions.issue(account))

    @app.post("/auth/register", response_model=AuthResponse, status_code=201, tags=["auth"])
    async def register(body: RegisterRequest) -> AuthResponse:
        try:
            account = state.accounts.register(
                email=body.email, password=body.password, display_name=body.display_name
            )
        except AccountError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return _issue(account)

    @app.post("/auth/refresh", response_model=AuthResponse, tags=["auth"])
    async def refresh(body: RefreshRequest) -> AuthResponse:
        from omni_modal.security.sessions import RefreshTokenError  # noqa: PLC0415

        try:
            return AuthResponse(**state.sessions.refresh(body.refresh_token))
        except RefreshTokenError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    @app.post("/auth/logout", tags=["auth"])
    async def logout(body: LogoutRequest) -> dict:
        return {"revoked": state.sessions.revoke(body.refresh_token)}

    @app.post("/auth/login", response_model=None, tags=["auth"])
    async def login(body: LoginRequest):
        account = state.accounts.authenticate(email=body.email, password=body.password)
        if account is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
        # Step-up: if the account has MFA, require the second factor before
        # issuing tokens. We stash the authenticated identity behind a one-time
        # nonce so /auth/mfa/login can complete the session after code check.
        if state.mfa.is_enrolled(account.tenant_id, account.user_id):
            nonce = state.sessions.stash(
                {
                    "tenant_id": account.tenant_id,
                    "user_id": account.user_id,
                    "email": account.email,
                    "roles": list(account.roles),
                    "expires_at": time.time() + MFA_CHALLENGE_SECONDS,
                },
                ttl=MFA_CHALLENGE_SECONDS,
            )
            return {"mfa_required": True, "mfa_token": nonce}
        return state.sessions.issue(account)

    @app.post("/auth/mfa/login", response_model=AuthResponse, tags=["auth"])
    async def mfa_login(body: MfaLoginRequest) -> AuthResponse:
        pending = state.sessions.unstash(body.mfa_token)
        if pending is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA challenge expired or invalid.")
        remaining = int(float(pending.get("expires_at", 0)) - time.time())
        if remaining <= 0:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA challenge expired or invalid.")
        try:
            accepted = state.mfa.verify(pending["tenant_id"], pending["user_id"], body.code)
        except MfaLocked as exc:
            minutes = max(1, round(exc.retry_after / 60))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": f"Too many wrong codes. Try again in about {minutes} "
                             f"minute{'s' if minutes != 1 else ''}.",
                    "mfa_token": None,
                },
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc
        if not accepted:
            # Re-stash so the user can retry with a fresh code. The retry token
            # keeps the original deadline, and MFA_MAX_ATTEMPTS wrong codes end
            # the challenge.
            attempts = int(pending.get("attempts", 0)) + 1
            new_nonce = None
            if attempts < MFA_MAX_ATTEMPTS:
                new_nonce = state.sessions.stash({**pending, "attempts": attempts}, ttl=remaining)
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Invalid MFA code.", "mfa_token": new_nonce},
            )
        from types import SimpleNamespace  # noqa: PLC0415

        account = SimpleNamespace(
            tenant_id=pending["tenant_id"], user_id=pending["user_id"],
            email=pending.get("email", ""), roles=tuple(pending.get("roles", [])),
        )
        return AuthResponse(**state.sessions.issue(account))

    @app.post("/auth/mfa/enroll", tags=["auth"])
    async def mfa_enroll(body: MfaEnrollRequest, claims: JwtClaims = Depends(current_claims)) -> dict:
        account = state.accounts.authenticate(email=body.email, password=body.password)
        if account is None or (account.tenant_id, account.user_id) != (claims.tenant_id, claims.user_id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Enter your current password to turn on two-step sign-in."
            )
        try:
            return state.mfa.begin_enroll(claims.tenant_id, claims.user_id, account.email)
        except MfaAlreadyEnrolled as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Two-step sign-in is already on. Turn it off first to set up a new authenticator.",
            ) from exc

    @app.post("/auth/mfa/confirm", tags=["auth"])
    async def mfa_confirm(body: MfaConfirmRequest, claims: JwtClaims = Depends(current_claims)) -> dict:
        if not state.mfa.confirm(claims.tenant_id, claims.user_id, body.code):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid code; MFA not activated.")
        return {"enrolled": True}

    @app.get("/auth/mfa/status", tags=["auth"])
    async def mfa_status(claims: JwtClaims = Depends(current_claims)) -> dict:
        return {"enrolled": state.mfa.is_enrolled(claims.tenant_id, claims.user_id)}

    @app.post("/auth/mfa/disable", tags=["auth"])
    async def mfa_disable(body: MfaConfirmRequest, claims: JwtClaims = Depends(current_claims)) -> dict:
        try:
            disabled = state.mfa.disable(claims.tenant_id, claims.user_id, body.code)
        except MfaLocked as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong codes. Try again later.",
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc
        if not disabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid code; MFA not disabled.")
        return {"enrolled": False}

    # ── Password reset ───────────────────────────────────────────────────
    def _audit(action: str, outcome: str, metadata: dict) -> None:
        """Record a security event. Auditing must never break the request."""
        try:
            if state.audit_sink is not None:
                state.audit_sink.record_event(None, action, "account", None, outcome, metadata)
        except Exception as exc:  # noqa: BLE001
            observability.capture_exception(exc, operation="api.audit")

    def _send_reset_email(to: str, token: str) -> None:
        base = os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")
        link = f"{base}/reset-password?token={token}"
        try:
            state.saas.email.send(
                to=to,
                subject="Reset your OMERO password",
                body=(
                    "Someone asked to reset the password for this OMERO account. "
                    f'Choose a new password here: <a href="{link}">{link}</a>. '
                    "This link works once and expires in 30 minutes. "
                    "If you did not ask for this, you can ignore this email."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            observability.capture_exception(exc, operation="api.password_reset_email")

    @app.post("/auth/password/forgot", tags=["auth"])
    async def forgot_password(body: ForgotPasswordRequest, background: BackgroundTasks) -> dict:
        # The response body is identical whether or not the address has an
        # account, and the slow part (sending the email) happens after the
        # response. This endpoint is therefore not a convenient oracle, but it
        # is not constant-time, and /auth/register still reports taken emails.
        account = state.accounts.find_account(body.email)
        if account is not None:
            token = state.password_resets.issue(account.email)
            if token is not None:
                background.add_task(_send_reset_email, account.email, token)
            _audit(
                "password_reset_requested", "sent" if token else "rate_limited",
                {"tenant_id": account.tenant_id, "user_id": account.user_id},
            )
        return {"sent": True}

    @app.post("/auth/password/reset", tags=["auth"])
    async def reset_password(body: ResetPasswordRequest) -> dict:
        # Validate the new password before spending the single-use link.
        if len(body.password) < 8:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password must be at least 8 characters.")
        email = state.password_resets.consume(body.token)
        account = state.accounts.set_password(email, body.password) if email else None
        if account is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_RESET_LINK)
        # Sign the account out everywhere: whoever held the old password is out.
        revoked = state.sessions.revoke_user(account.tenant_id, account.user_id)
        _audit(
            "password_reset_completed", "ok",
            {"tenant_id": account.tenant_id, "user_id": account.user_id, "sessions_revoked": revoked},
        )
        return {"reset": True}

    # ── Query ────────────────────────────────────────────────────────────
    @app.post("/query", tags=["research"], dependencies=[Depends(heavy_request)])
    async def query(body: QueryRequestModel, claims: JwtClaims = Depends(require_roles("/query"))) -> dict:
        meter(claims, "queries")
        req = QueryRequest(
            tenant_id=claims.tenant_id, user_id=claims.user_id, question=body.question,
            top_k=body.top_k, min_similarity=body.min_similarity,
            external_permit=_web_permit(claims),
        )
        try:
            result = state.workflow.answer(req)
        except Exception as exc:
            observability.capture_exception(exc, operation="api.query")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Query failed.") from exc
        return {**result.response.to_json_dict(), "trace": result.trace}

    # These handlers are plain ``def``: FastAPI runs them in a worker thread,
    # so the blocking session-store, metering and model calls never stall the
    # event loop. A sync generator passed to StreamingResponse is iterated in a
    # worker thread too.
    def _load_session(claims: JwtClaims, session_id: str | None, kind: str) -> tuple[list[tuple[str, str]], str | None]:
        """Earlier turns and project of a caller-owned session of ``kind``; 404 for anyone else's."""
        if not session_id:
            return [], None
        try:
            detail = state.research_sessions.get(claims.tenant_id, claims.user_id, session_id)
        except SessionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.") from exc
        if detail.session.kind != kind:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
        history = [(m.role, m.content) for m in detail.messages[-2 * HISTORY_TURNS:]]
        return history, detail.session.project_id

    def _load_history(claims: JwtClaims, session_id: str | None, kind: str) -> list[tuple[str, str]]:
        return _load_session(claims, session_id, kind)[0]

    def _project_scope(claims: JwtClaims, project_id: str | None) -> tuple[str, ...] | None:
        """The document ids a project limits research to; 404 for another tenant's project."""
        if not project_id:
            return None
        try:
            return state.projects.scope(claims.tenant_id, project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.") from exc

    def _session_for(claims: JwtClaims, session_id: str | None, kind: str, first_text: str,
                     project_id: str | None = None) -> str:
        if session_id:
            return session_id
        return state.research_sessions.create(
            claims.tenant_id, claims.user_id, title=first_text, kind=kind, project_id=project_id,
        ).id

    class _Turn:
        """What one streamed answer has produced so far, saved exactly once."""

        def __init__(self, claims: JwtClaims, session_id: str, question: str) -> None:
            self.claims, self.session_id, self.question = claims, session_id, question
            self.parts: list[str] = []
            self.saved = False

        def save(self, answer: str, answer_status: str, citations: list[dict]) -> None:
            if self.saved:
                return
            self.saved = True
            try:
                state.research_sessions.append_turn(
                    self.claims.tenant_id, self.claims.user_id, self.session_id,
                    question=self.question, answer=answer, status=answer_status, citations=citations,
                )
            except SessionNotFound:
                pass  # deleted while the answer was streaming
            except Exception as exc:  # noqa: BLE001 - the answer was delivered; saving is best-effort
                observability.capture_exception(exc, operation="api.save_turn")

        def save_unfinished(self) -> None:
            """The client left, or the stream broke, before the answer finished."""
            partial = "".join(self.parts).strip()
            if partial:
                self.save(partial + "\n\n_This answer was interrupted before it finished._", "interrupted", [])
            else:
                self.save("No answer was produced: the request ended before it finished.", "failed", [])

    @app.post("/query/stream", tags=["research"], dependencies=[Depends(heavy_request)])
    def query_stream(body: QueryRequestModel, claims: JwtClaims = Depends(require_roles("/query/stream"))):
        history, session_project = _load_session(claims, body.session_id, "research")
        project_id = session_project if body.session_id else body.project_id
        scope = _project_scope(claims, project_id)
        meter(claims, "queries")
        session_id = _session_for(claims, body.session_id, "research", body.question, project_id)
        turn = _Turn(claims, session_id, body.question)

        def _events():
            try:
                yield _sse("session", {"session_id": session_id})
                search_query = state.rewriter.rewrite(body.question, history) if history else None
                req = QueryRequest(
                    tenant_id=claims.tenant_id, user_id=claims.user_id, question=body.question,
                    top_k=body.top_k, min_similarity=body.min_similarity,
                    history=tuple(history), search_query=search_query, document_ids=scope,
                    external_permit=_web_permit(claims),
                )
                result = None
                try:
                    for item in state.workflow.answer_stream(req):
                        if isinstance(item, AgentWorkflowResult):
                            result = item
                        else:
                            turn.parts.append(item)
                            yield _sse("delta", {"delta": item})
                except Exception as exc:  # noqa: BLE001
                    observability.capture_exception(exc, operation="api.query_stream")
                    turn.save_unfinished()
                    yield _sse("error", {"error": "Query failed.", "session_id": session_id})
                    return
                response = result.response.to_json_dict()
                turn.save(response["answer_markdown"], response["status"], response["citations"])
                yield _sse("done", {**response, "trace": result.trace, "session_id": session_id,
                                    "search_query": search_query, "project_id": project_id})
            finally:
                # Runs on normal completion (no-op), errors, and client disconnects.
                turn.save_unfinished()

        return StreamingResponse(_events(), media_type="text/event-stream", headers=_SSE_HEADERS)

    # ── Saved sessions ───────────────────────────────────────────────────
    @app.get("/research/sessions", tags=["research"])
    def list_sessions(kind: str | None = None,
                      claims: JwtClaims = Depends(require_roles("/research/sessions"))) -> dict:
        if kind not in (None, "research", "assistant"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "kind must be research or assistant.")
        items = state.research_sessions.list(claims.tenant_id, claims.user_id, kind=kind)
        return {"sessions": [s.to_dict() for s in items], "total": len(items)}

    @app.post("/research/sessions", status_code=201, tags=["research"])
    def create_session(body: CreateSessionRequest,
                       claims: JwtClaims = Depends(require_roles("/research/sessions"))) -> dict:
        return state.research_sessions.create(
            claims.tenant_id, claims.user_id, title=body.title, kind=body.kind
        ).to_dict()

    @app.get("/research/sessions/{session_id}", tags=["research"])
    def get_session(session_id: str,
                    claims: JwtClaims = Depends(require_roles("/research/sessions/"))) -> dict:
        try:
            return state.research_sessions.get(claims.tenant_id, claims.user_id, session_id).to_dict()
        except SessionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.") from exc

    @app.patch("/research/sessions/{session_id}", tags=["research"])
    def rename_session(session_id: str, body: RenameSessionRequest,
                       claims: JwtClaims = Depends(require_roles("/research/sessions/"))) -> dict:
        try:
            return state.research_sessions.rename(
                claims.tenant_id, claims.user_id, session_id, body.title
            ).to_dict()
        except SessionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.") from exc

    @app.delete("/research/sessions/{session_id}", tags=["research"])
    def delete_session(session_id: str,
                       claims: JwtClaims = Depends(require_roles("/research/sessions/"))) -> dict:
        try:
            state.research_sessions.delete(claims.tenant_id, claims.user_id, session_id)
        except SessionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.") from exc
        return {"deleted": True}

    # ── Web research setting ─────────────────────────────────────────────
    def _web_permit(claims: JwtClaims):
        """Asked by the workflow only when the documents cannot answer a question."""
        def permit() -> str | None:
            limiter = state.heavy_rate_limiter
            return state.saas.external_research_permit(
                tenant_id=claims.tenant_id, user_id=claims.user_id,
                throttle=(lambda: limiter.check_delegation(claims.tenant_id)) if limiter is not None else None,
            )
        return permit

    def _research_settings(tenant_id: str, user_id: str) -> dict:
        org = state.saas.ensure_org(tenant_id, owner_user_id=user_id)
        plan = get_plan(org.plan_id)
        return {
            "external_research_enabled": org.external_research_enabled,
            "provider_configured": state.web_search is not None,
            "plan_includes": plan.has_feature(FEATURE_EXTERNAL_DELEGATION),
            "monthly_limit": plan.limit_for("external_research"),
            "used_this_month": state.saas.usage.get(tenant_id, "external_research"),
        }

    @app.get("/workspace/research-settings", tags=["workspace"])
    def get_research_settings(claims: JwtClaims = Depends(current_claims)) -> dict:
        return _research_settings(claims.tenant_id, claims.user_id)

    @app.put("/workspace/research-settings", tags=["workspace"])
    def put_research_settings(body: ResearchSettingsRequest,
                              claims: JwtClaims = Depends(require_roles("/workspace/settings"))) -> dict:
        state.saas.set_external_research(tenant_id=claims.tenant_id, user_id=claims.user_id,
                                         enabled=body.external_research_enabled)
        return _research_settings(claims.tenant_id, claims.user_id)

    # ── Site-wide assistant (read-only) ──────────────────────────────────
    @app.post("/assistant/stream", tags=["assistant"], dependencies=[Depends(heavy_request)])
    def assistant_stream(body: AssistantRequest, claims: JwtClaims = Depends(current_claims)):
        history = _load_history(claims, body.session_id, "assistant")
        meter(claims, "queries")
        session_id = _session_for(claims, body.session_id, "assistant", body.message)
        turn = _Turn(claims, session_id, body.message)
        ctx = AssistantContext(
            tenant_id=claims.tenant_id, user_id=claims.user_id,
            roles=tuple(claims.roles), page_path=body.page or "",
        )

        def _events():
            try:
                yield _sse("session", {"session_id": session_id})
                done = None
                try:
                    for name, payload in state.assistant.respond_stream(ctx, body.message, history):
                        if name == "done":
                            done = payload
                        else:
                            if name == "delta":
                                turn.parts.append(payload["delta"])
                            yield _sse(name, payload)
                except Exception as exc:  # noqa: BLE001
                    observability.capture_exception(exc, operation="api.assistant_stream")
                    turn.save_unfinished()
                    yield _sse("error", {"error": "The assistant failed.", "session_id": session_id})
                    return
                turn.save(done["answer_markdown"], "answered", done["citations"])
                yield _sse("done", {**done, "session_id": session_id})
            finally:
                turn.save_unfinished()

        return StreamingResponse(_events(), media_type="text/event-stream", headers=_SSE_HEADERS)

    # ── SaaS reads ───────────────────────────────────────────────────────
    @app.get("/workspaces", tags=["saas"])
    async def workspaces(claims: JwtClaims = Depends(current_claims)) -> dict:
        org = state.saas.ensure_org(claims.tenant_id, owner_user_id=claims.user_id)
        ws = [w.to_dict() for w in state.saas.workspaces.list_workspaces(org.id)]
        return {"organization": org.to_dict(), "workspaces": ws, "total": len(ws)}

    @app.get("/usage", tags=["saas"])
    async def usage(claims: JwtClaims = Depends(current_claims)) -> dict:
        state.saas.ensure_org(claims.tenant_id, owner_user_id=claims.user_id)
        return state.saas.usage_report(claims.tenant_id)

    @app.get("/billing", tags=["saas"])
    async def billing(claims: JwtClaims = Depends(current_claims)) -> dict:
        org = state.saas.ensure_org(claims.tenant_id, owner_user_id=claims.user_id)
        return {
            "billing_mode": state.saas.billing_mode(),
            "current_plan": org.plan_id,
            "plans": [p.to_dict() for p in PLANS.values()],
            "usage": state.saas.usage_report(claims.tenant_id),
        }

    @app.get("/notifications", tags=["saas"])
    async def notifications(claims: JwtClaims = Depends(current_claims)) -> dict:
        notes = state.saas.notifications.list_for(claims.tenant_id, user_id=claims.user_id)
        return {
            "notifications": [n.to_dict() for n in notes],
            "unread": state.saas.notifications.unread_count(claims.tenant_id, user_id=claims.user_id),
            "total": len(notes),
        }

    @app.get("/plans", tags=["saas"])
    async def plans() -> dict:
        return {"plans": [p.to_dict() for p in PLANS.values()]}

    @app.get("/members", tags=["saas"])
    async def members(claims: JwtClaims = Depends(current_claims)) -> dict:
        org = state.saas.ensure_org(claims.tenant_id, owner_user_id=claims.user_id)
        ms = [m.to_dict() for m in state.saas.workspaces.list_members(org.id)]
        invs = [i.to_dict() for i in state.saas.workspaces.list_invites(org.id) if i.status == "pending"]
        return {"members": ms, "invites": invs, "total": len(ms)}

    @app.get("/admin/stats", tags=["admin"])
    async def admin_stats(claims: JwtClaims = Depends(require_roles("/billing/change-plan"))) -> dict:
        org = state.saas.ensure_org(claims.tenant_id, owner_user_id=claims.user_id)
        analytics = state.saas.analytics
        return {
            "organization": org.to_dict(),
            "members": state.saas.workspaces.count_members(org.id),
            "workspaces": state.saas.workspaces.count_workspaces(org.id),
            "usage": state.saas.usage.snapshot(claims.tenant_id),
            "event_counts": analytics.event_counts() if hasattr(analytics, "event_counts") else {},
            "adapters": {
                "storage": state.saas.storage.backend,
                "email": state.saas.email.backend,
                "analytics": state.saas.analytics.backend,
            },
            "billing_mode": state.saas.billing_mode(),
            "audit_events": len(state.audit_sink.entries) if state.audit_sink else 0,
        }

    @app.get("/invites/preview", tags=["saas"])
    async def preview_invite(token: str) -> dict:
        preview = state.saas.preview_invite(token)
        if preview is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invite not found.")
        return preview

    # ── SaaS writes / actions ────────────────────────────────────────────
    @app.post("/workspaces", status_code=201, tags=["saas"])
    async def create_workspace(
        body: CreateWorkspaceRequest,
        claims: JwtClaims = Depends(require_roles("/workspaces")),
    ) -> dict:
        try:
            ws = state.saas.create_workspace(
                tenant_id=claims.tenant_id, user_id=claims.user_id, name=body.name
            )
        except PlanLimitExceeded as exc:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc
        return ws.to_dict()

    @app.post("/invites", status_code=201, tags=["saas"])
    async def create_invite(
        body: InviteRequest, claims: JwtClaims = Depends(require_roles("/invites"))
    ) -> dict:
        try:
            invite = state.saas.invite_member(
                tenant_id=claims.tenant_id, user_id=claims.user_id,
                email=body.email, role=body.role,
            )
        except PlanLimitExceeded as exc:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc
        data = invite.to_dict(include_token=True)
        data["accept_url"] = f"/accept-invite?token={invite.token}"
        return data

    @app.post("/invites/accept", tags=["saas"])
    async def accept_invite(body: AcceptInviteRequest, claims: JwtClaims = Depends(current_claims)) -> dict:
        member = state.saas.accept_invite(token=body.token, user_id=claims.user_id)
        if member is None:
            raise HTTPException(status.HTTP_410_GONE, "Invite is invalid, expired, or already used.")
        return member.to_dict()

    @app.post("/billing/change-plan", tags=["billing"])
    async def change_plan(
        body: ChangePlanRequest, claims: JwtClaims = Depends(require_roles("/billing/change-plan"))
    ) -> dict:
        org = state.saas.change_plan(tenant_id=claims.tenant_id, user_id=claims.user_id, plan_id=body.plan_id)
        if org is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan_id: {body.plan_id!r}.")
        return {"organization": org.to_dict(), "billing_mode": state.saas.billing_mode()}

    def _billing_urls() -> tuple[str, str]:
        base = os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")
        return (f"{base}/billing?status=success&session_id={{CHECKOUT_SESSION_ID}}",
                f"{base}/billing?status=cancelled")

    @app.post("/billing/checkout", tags=["billing"])
    async def checkout(body: CheckoutRequest, claims: JwtClaims = Depends(require_roles("/billing/checkout"))) -> dict:
        success_url, cancel_url = _billing_urls()
        try:
            result = state.saas.start_checkout(
                tenant_id=claims.tenant_id, user_id=claims.user_id, plan_id=body.plan_id,
                success_url=success_url, cancel_url=cancel_url,
            )
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        if result is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Plan {body.plan_id!r} is not purchasable.")
        return result

    @app.post("/billing/confirm", tags=["billing"])
    async def confirm(body: ConfirmRequest, claims: JwtClaims = Depends(require_roles("/billing/confirm"))) -> dict:
        try:
            result = state.saas.confirm_checkout(
                tenant_id=claims.tenant_id, user_id=claims.user_id, session_id=body.session_id
            )
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return result or {"paid": False}

    @app.post("/billing/portal", tags=["billing"])
    async def portal(claims: JwtClaims = Depends(require_roles("/billing/portal"))) -> dict:
        base = os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")
        try:
            result = state.saas.start_portal(
                tenant_id=claims.tenant_id, user_id=claims.user_id, return_url=f"{base}/billing"
            )
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        if result is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No Stripe customer yet. Subscribe first.")
        return result

    @app.post("/notifications/read", tags=["saas"])
    async def notifications_read(body: NotificationsReadRequest, claims: JwtClaims = Depends(current_claims)) -> dict:
        if body.all:
            count = state.saas.notifications.mark_all_read(claims.tenant_id, user_id=claims.user_id)
            return {"marked_read": count}
        if not body.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "id is required (or pass all=true).")
        ok = state.saas.notifications.mark_read(claims.tenant_id, body.id)
        return {"marked_read": 1 if ok else 0}

    # ── Ingestion ────────────────────────────────────────────────────────
    # ── Uploads and documents ────────────────────────────────────────────
    # Plain ``def`` handlers run in a worker thread, so store and storage calls
    # never block the event loop.
    def _documents_call(fn, *args, **kwargs):
        """Run a DocumentService call, turning its errors into HTTP answers."""
        try:
            return fn(*args, **kwargs)
        except DocumentNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.") from exc
        except Forbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except InvalidState as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except UploadRejected as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    def _document_payload(doc, job) -> dict:
        return {"document": doc.to_dict(), "job": job.to_dict() if job else None}

    @app.post("/documents/uploads", tags=["documents"], dependencies=[Depends(heavy_request)])
    def create_upload(body: CreateUploadRequest, response: Response,
                      claims: JwtClaims = Depends(require_roles("/ingest/upload"))) -> dict:
        """Start an upload: returns where to send the file, or the existing copy of it."""
        _documents_call(state.documents.validate_upload, file_name=body.filename,
                        size_bytes=body.size_bytes, sha256=body.sha256)
        if state.documents.find_duplicate(claims.tenant_id, body.sha256) is None:
            meter(claims, "uploads")  # only valid, new documents count against the plan
        slot = _documents_call(
            state.documents.create_upload, claims.tenant_id, claims.user_id,
            file_name=body.filename, size_bytes=body.size_bytes, content_type=body.content_type,
            sha256=body.sha256, title=body.title,
        )
        if slot.duplicate:
            _, job = state.documents.get(claims.tenant_id, slot.document.id)
            return {**_document_payload(slot.document, job), "duplicate": True, "upload": None}
        if body.workspace_id:
            state.saas.tag_document(slot.document.id, body.workspace_id)
        response.status_code = status.HTTP_201_CREATED
        upload = (
            {"method": "POST", "url": slot.upload["url"], "fields": slot.upload["fields"], "requires_auth": False}
            if slot.upload
            else {"method": "PUT", "url": f"/documents/{slot.document.id}/content", "requires_auth": True}
        )
        _, job = state.documents.get(claims.tenant_id, slot.document.id)
        return {**_document_payload(slot.document, job), "duplicate": False, "resumed": slot.resumed,
                "upload": upload}

    @app.put("/documents/{document_id}/content", tags=["documents"])
    async def upload_content(document_id: str, request: Request,
                             claims: JwtClaims = Depends(require_roles("/ingest/upload"))) -> dict:
        """Receive the file when no object store is configured.

        Who may upload is checked before a byte is read; the body is streamed to
        a temp file and reading stops as soon as it passes the declared size.
        """
        doc = await run_in_threadpool(
            _documents_call, state.documents.check_can_upload, claims.tenant_id, claims.user_id, document_id,
        )
        too_large = HTTPException(413,
                                  "The file is larger than the size declared for this upload.")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > doc.size_bytes:
            raise too_large
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".upload")
        path = Path(handle.name)
        try:
            total = 0
            with handle:
                async for part in request.stream():
                    total += len(part)
                    if total > doc.size_bytes:
                        raise too_large
                    handle.write(part)
            stored = await run_in_threadpool(
                _documents_call, state.documents.accept_file, claims.tenant_id, claims.user_id, document_id, path,
            )
        finally:
            path.unlink(missing_ok=True)
        return {"received_bytes": stored}

    @app.post("/documents/{document_id}/complete", status_code=202, tags=["documents"])
    def complete_upload(document_id: str, claims: JwtClaims = Depends(require_roles("/ingest/upload"))) -> dict:
        doc, job = _documents_call(state.documents.complete_upload, claims.tenant_id, claims.user_id, document_id)
        state.saas.analytics.capture(event="upload", tenant_id=claims.tenant_id, user_id=claims.user_id,
                                     properties={"source_kind": doc.source_kind})
        return _document_payload(doc, job)

    @app.get("/documents/{document_id}", tags=["documents"])
    def get_document(document_id: str, claims: JwtClaims = Depends(require_roles("/documents"))) -> dict:
        doc, job = _documents_call(state.documents.get, claims.tenant_id, document_id)
        return _document_payload(doc, job)

    @app.delete("/documents/{document_id}", tags=["documents"])
    def delete_document(document_id: str, claims: JwtClaims = Depends(require_roles("/documents/manage"))) -> dict:
        _documents_call(state.documents.delete, claims.tenant_id, claims.user_id, claims.roles, document_id)
        state.projects.forget_document(claims.tenant_id, document_id)
        return {"deleted": True}

    @app.post("/documents/{document_id}/reprocess", status_code=202, tags=["documents"], dependencies=[Depends(heavy_request)])
    def reprocess_document(document_id: str,
                           claims: JwtClaims = Depends(require_roles("/documents/manage"))) -> dict:
        job = _documents_call(state.documents.reprocess, claims.tenant_id, claims.user_id, claims.roles, document_id)
        doc, _ = state.documents.get(claims.tenant_id, document_id)
        return _document_payload(doc, job)

    @app.get("/documents/{document_id}/download", tags=["documents"])
    def download_document(document_id: str, claims: JwtClaims = Depends(require_roles("/documents"))) -> dict:
        url, doc = _documents_call(state.documents.download, claims.tenant_id, document_id)
        if url:
            return {"url": url, "requires_auth": False, "expires_in": 300, "file_name": doc.file_name}
        return {"url": f"/documents/{doc.id}/content", "requires_auth": True, "file_name": doc.file_name}

    @app.get("/documents/{document_id}/content", tags=["documents"])
    def document_content(document_id: str, claims: JwtClaims = Depends(require_roles("/documents"))):
        """Serve the original file when no object store is configured."""
        _, doc = _documents_call(state.documents.download, claims.tenant_id, document_id)
        path = _documents_call(state.documents.local_path, claims.tenant_id, document_id)
        return FileResponse(path, media_type=doc.content_type or "application/octet-stream",
                            filename=safe_download_name(doc.file_name))

    @app.post("/ingest/upload", status_code=202, tags=["ingestion"], dependencies=[Depends(heavy_request)])
    def ingest_upload(body: UploadRequest, claims: JwtClaims = Depends(require_roles("/ingest/upload"))) -> dict:
        """One-call upload with the file inside the JSON body (scripts and API clients)."""
        try:
            content = base64.b64decode(body.content_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "content_base64 is not valid base64.") from exc
        digest = hashlib.sha256(content).hexdigest()
        _documents_call(state.documents.validate_upload, file_name=body.filename,
                        size_bytes=len(content), sha256=digest)
        if state.documents.find_duplicate(claims.tenant_id, digest) is None:
            meter(claims, "uploads")
        doc, job, duplicate = _documents_call(
            state.documents.ingest_bytes, claims.tenant_id, claims.user_id,
            file_name=body.filename, data=content, title=body.title,
        )
        if body.workspace_id and not duplicate:
            state.saas.tag_document(doc.id, body.workspace_id)
        if job is None:
            _, job = state.documents.get(claims.tenant_id, doc.id)
        return {"job_id": job.id if job else None, "status": job.to_dict()["status"] if job else doc.status,
                "document_id": doc.id, "duplicate": duplicate}

    @app.post("/ingest/local", status_code=202, tags=["ingestion"])
    def ingest_local(body: dict, claims: JwtClaims = Depends(require_roles("/ingest/local"))) -> dict:
        """Ingest a file already on the server. Development only: it reads server paths."""
        if os.environ.get("OMERO_ALLOW_LOCAL_INGEST", "false").lower() != "true":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
        file_path = Path(str(body.get("file_path", "")))
        if not file_path.is_file():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "file_path must point to an existing file.")
        doc, job, duplicate = _documents_call(
            state.documents.ingest_bytes, claims.tenant_id, claims.user_id,
            file_name=file_path.name, data=file_path.read_bytes(), title=body.get("title"),
        )
        if job is None:
            _, job = state.documents.get(claims.tenant_id, doc.id)
        return {"job_id": job.id if job else None, "status": job.to_dict()["status"] if job else doc.status,
                "document_id": doc.id, "duplicate": duplicate}

    @app.get("/ingest/jobs/{job_id}", tags=["ingestion"])
    def job_status(job_id: str, claims: JwtClaims = Depends(current_claims)) -> dict:
        try:
            job = state.documents.get_job(claims.tenant_id, job_id)
        except DocumentNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.") from exc
        return {**job.to_dict(), "error_code": None}

    # ── Document library ─────────────────────────────────────────────────
    def _ws_filter(workspace_id: str | None):
        return state.saas.documents_in_workspace(workspace_id) if workspace_id else None

    def _library_page(claims: JwtClaims, *, archived: bool, q: str, status_: str | None, kind: str | None,
                      project_id: str | None, mine: bool, sort: str, page: int, page_size: int,
                      workspace_id: str | None) -> dict:
        scope: set[str] | None = None
        if project_id:
            scope = set(_projects_call(state.projects.scope, claims.tenant_id, project_id))
        workspace = _ws_filter(workspace_id)
        if workspace is not None:
            scope = set(workspace) if scope is None else scope & set(workspace)
        docs, total = _documents_call(state.documents.query, claims.tenant_id, DocumentQuery(
            text=q, status=status_, source_kind=kind, uploaded_by=claims.user_id if mine else None,
            archived=archived, document_ids=frozenset(scope) if scope is not None else None,
            sort=sort, page=page, page_size=page_size,
        ))
        names = {p.id: p.name for p in state.projects.projects(claims.tenant_id, include_archived=True)}
        memberships = state.projects.projects_for_documents(claims.tenant_id, [d.id for d in docs])
        items = [
            {**d.to_dict(), "projects": [{"id": pid, "name": names.get(pid, "")} for pid in memberships.get(d.id, [])]}
            for d in docs
        ]
        return {"documents": items, "total": total, "page": max(1, page), "page_size": max(1, min(page_size, 200))}

    @app.get("/documents", tags=["documents"])
    def documents(q: str = "", status: str | None = None, kind: str | None = None,  # noqa: A002
                  project_id: str | None = None, mine: bool = False, sort: str = "newest",
                  page: int = 1, page_size: int = 25, workspace_id: str | None = None,
                  claims: JwtClaims = Depends(require_roles("/documents"))) -> dict:
        """The library: search titles and file names, filter, sort and page. Archived documents are left out."""
        return _library_page(claims, archived=False, q=q, status_=status, kind=kind, project_id=project_id,
                             mine=mine, sort=sort, page=page, page_size=page_size, workspace_id=workspace_id)

    @app.post("/documents/{document_id}/archive", tags=["documents"])
    def archive_document(document_id: str, claims: JwtClaims = Depends(require_roles("/documents/manage"))) -> dict:
        doc = _documents_call(state.documents.archive, claims.tenant_id, claims.user_id, claims.roles, document_id)
        return {"document": doc.to_dict()}

    @app.post("/documents/{document_id}/restore", tags=["documents"])
    def restore_document(document_id: str, claims: JwtClaims = Depends(require_roles("/documents/manage"))) -> dict:
        doc = _documents_call(state.documents.restore, claims.tenant_id, claims.user_id, claims.roles, document_id)
        return {"document": doc.to_dict()}

    # ── Projects ─────────────────────────────────────────────────────────
    def _projects_call(fn, *args, **kwargs):
        """Run a ProjectService call, turning its errors into HTTP answers."""
        try:
            return fn(*args, **kwargs)
        except (ProjectNotFound, SessionNotFound) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.") from exc
        except DocumentNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.") from exc
        except Forbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except InvalidState as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ProjectRejected as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get("/projects", tags=["projects"])
    def list_projects(include_archived: bool = False,
                      claims: JwtClaims = Depends(require_roles("/projects"))) -> dict:
        entries = state.projects.list(claims.tenant_id, include_archived=include_archived)
        projects = [{**e["project"].to_dict(), "counts": e["counts"]} for e in entries]
        return {"projects": projects, "total": len(projects)}

    @app.post("/projects", status_code=201, tags=["projects"])
    def create_project(body: ProjectRequest, claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        project = _projects_call(state.projects.create, claims.tenant_id, claims.user_id,
                                 name=body.name or "", description=body.description or "")
        return project.to_dict()

    @app.get("/projects/{project_id}", tags=["projects"])
    def get_project(project_id: str, claims: JwtClaims = Depends(require_roles("/projects"))) -> dict:
        tenant = claims.tenant_id
        project = _projects_call(state.projects.get, tenant, project_id)
        documents = state.projects.documents(tenant, project_id)
        return {
            "project": project.to_dict(),
            "documents": [d.to_dict() for d in documents],
            "items": [i.to_dict() for i in state.projects.items(tenant, project_id)],
            "reports": [r.to_dict(with_markdown=False) for r in state.projects.reports(tenant, project_id)],
            "sessions": [s.to_dict() for s in state.projects.sessions(tenant, claims.user_id, project_id)],
            "insights": state.projects.insights(tenant, project_id),
            "activity": [a.to_dict() for a in state.projects.activity(tenant, project_id, limit=30)],
        }

    @app.patch("/projects/{project_id}", tags=["projects"])
    def update_project(project_id: str, body: ProjectRequest,
                       claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        return _projects_call(state.projects.update, claims.tenant_id, claims.user_id, project_id,
                              name=body.name, description=body.description).to_dict()

    @app.post("/projects/{project_id}/archive", tags=["projects"])
    def archive_project(project_id: str, claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        return _projects_call(state.projects.archive, claims.tenant_id, claims.user_id, claims.roles,
                              project_id).to_dict()

    @app.post("/projects/{project_id}/restore", tags=["projects"])
    def restore_project(project_id: str, claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        return _projects_call(state.projects.restore, claims.tenant_id, claims.user_id, claims.roles,
                              project_id).to_dict()

    @app.post("/projects/{project_id}/documents", tags=["projects"])
    def add_project_document(project_id: str, body: ProjectDocumentRequest,
                             claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        _projects_call(state.projects.add_document, claims.tenant_id, claims.user_id, project_id, body.document_id)
        return {"added": True}

    @app.delete("/projects/{project_id}/documents/{document_id}", tags=["projects"])
    def remove_project_document(project_id: str, document_id: str,
                                claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        _projects_call(state.projects.remove_document, claims.tenant_id, claims.user_id, project_id, document_id)
        return {"removed": True}

    @app.post("/projects/{project_id}/items", status_code=201, tags=["projects"])
    def add_project_item(project_id: str, body: ProjectItemRequest,
                         claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        item = _projects_call(
            state.projects.add_item, claims.tenant_id, claims.user_id, project_id, kind=body.kind,
            title=body.title, body=body.body or "", citations=body.citations or [],
            source_session_id=body.source_session_id,
        )
        return item.to_dict()

    @app.patch("/projects/{project_id}/items/{item_id}", tags=["projects"])
    def update_project_item(project_id: str, item_id: str, body: ProjectItemUpdate,
                            claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        return _projects_call(state.projects.update_item, claims.tenant_id, claims.user_id, claims.roles,
                              project_id, item_id, title=body.title, body=body.body).to_dict()

    @app.delete("/projects/{project_id}/items/{item_id}", tags=["projects"])
    def delete_project_item(project_id: str, item_id: str,
                            claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        _projects_call(state.projects.delete_item, claims.tenant_id, claims.user_id, claims.roles,
                       project_id, item_id)
        return {"deleted": True}

    @app.get("/projects/{project_id}/search", tags=["projects"])
    def search_project(project_id: str, q: str = "", claims: JwtClaims = Depends(require_roles("/projects"))) -> dict:
        _projects_call(state.projects.get, claims.tenant_id, project_id)
        return state.projects.search(claims.tenant_id, project_id, q[:200])

    @app.post("/projects/{project_id}/reports", status_code=201, tags=["projects"])
    def create_project_report(project_id: str, claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        return _projects_call(state.projects.report_for_project, claims.tenant_id, claims.user_id,
                              project_id).to_dict()

    @app.post("/research/sessions/{session_id}/report", status_code=201, tags=["projects"])
    def create_session_report(session_id: str,
                              claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        return _projects_call(state.projects.report_for_session, claims.tenant_id, claims.user_id,
                              session_id).to_dict()

    @app.put("/research/sessions/{session_id}/project", tags=["projects"])
    def set_session_project(session_id: str, body: SessionProjectRequest,
                            claims: JwtClaims = Depends(require_roles("/projects/manage"))) -> dict:
        return _projects_call(state.projects.link_session, claims.tenant_id, claims.user_id,
                              body.project_id, session_id).to_dict()

    @app.get("/reports", tags=["projects"])
    def my_reports(claims: JwtClaims = Depends(require_roles("/projects"))) -> dict:
        reports_ = state.projects.my_reports(claims.tenant_id, claims.user_id)
        return {"reports": [r.to_dict(with_markdown=False) for r in reports_]}

    @app.get("/reports/{report_id}", tags=["projects"])
    def get_report(report_id: str, claims: JwtClaims = Depends(require_roles("/projects"))) -> dict:
        return _projects_call(state.projects.get_report, claims.tenant_id, claims.user_id, report_id).to_dict()

    @app.get("/reports/{report_id}/download", tags=["projects"])
    def download_report(report_id: str, claims: JwtClaims = Depends(require_roles("/projects"))):
        report = _projects_call(state.projects.get_report, claims.tenant_id, claims.user_id, report_id)
        return Response(report.markdown, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": content_disposition(f"{report.title}.md")})

    @app.get("/archives", tags=["documents"])
    def archives(q: str = "", kind: str | None = None, sort: str = "newest", page: int = 1, page_size: int = 25,
                 workspace_id: str | None = None, claims: JwtClaims = Depends(require_roles("/archives"))) -> dict:
        """Archived documents: kept, but left out of research until restored."""
        return _library_page(claims, archived=True, q=q, status_=None, kind=kind, project_id=None, mine=False,
                             sort=sort, page=page, page_size=page_size, workspace_id=workspace_id)

    # ── Dashboard and search ─────────────────────────────────────────────
    @app.get("/dashboard", tags=["workspace"])
    def dashboard(claims: JwtClaims = Depends(current_claims)) -> dict:
        """Counts and recent activity, all read from stored records."""
        tenant = claims.tenant_id
        counts = state.documents.status_counts(tenant)
        archived_count = counts.pop("archived")
        by_status = counts
        recent, _ = state.documents.query(tenant, DocumentQuery(page_size=5))
        projects = state.projects.projects(tenant)
        names = {p.id: p.name for p in state.projects.projects(tenant, include_archived=True)}
        sessions = state.research_sessions.list(tenant, claims.user_id, kind="research", limit=5)
        state.saas.ensure_org(tenant, owner_user_id=claims.user_id)
        return {
            "documents": {**by_status, "archived": archived_count, "total": sum(by_status.values())},
            "projects": len(projects),
            # First-run checklist: has each step of the main journey happened yet?
            "progress": {
                "uploaded": sum(counts.values()) + archived_count > 0,
                "asked": bool(sessions),
                "project": bool(names),
                "report": bool(state.projects.my_reports(tenant, claims.user_id)),
            },
            "recent_documents": [d.to_dict() for d in recent],
            "recent_sessions": [s.to_dict() for s in sessions],
            "recent_activity": [
                {**a.to_dict(), "project_name": names.get(a.project_id)}
                for a in state.projects.activity(tenant, None, limit=10)
            ],
            "usage": state.saas.usage_report(tenant),
        }

    @app.get("/search", tags=["workspace"])
    def global_search(q: str = "", claims: JwtClaims = Depends(current_claims)) -> dict:
        """Search document titles and file names, project names and descriptions, and your own research titles."""
        text = q.strip()[:200]
        empty = {"documents": [], "projects": [], "sessions": []}
        scope = "Document titles and file names, project names and descriptions, and your research session titles."
        if len(text) < 2:
            return {**empty, "scope": scope}
        lower = text.lower()
        documents: list[dict] = []
        try:
            assert_endpoint_roles("/documents", claims.roles)
            docs, _ = state.documents.query(claims.tenant_id, DocumentQuery(text=text, page_size=8))
            documents = [{"id": d.id, "title": d.title, "status": d.status} for d in docs]
        except RbacError:
            pass
        projects = [
            {"id": p.id, "name": p.name}
            for p in state.projects.projects(claims.tenant_id)
            if lower in p.name.lower() or lower in p.description.lower()
        ][:8]
        sessions = [
            {"id": s.id, "title": s.title}
            for s in state.research_sessions.list(claims.tenant_id, claims.user_id, kind="research", limit=200)
            if lower in s.title.lower()
        ][:8]
        return {"documents": documents, "projects": projects, "sessions": sessions, "scope": scope}

    # ── Billing history and ingestion failures ───────────────────────────
    @app.get("/billing/invoices", tags=["billing"])
    def billing_invoices(claims: JwtClaims = Depends(require_roles("/billing/portal"))) -> dict:
        org = state.saas.ensure_org(claims.tenant_id, owner_user_id=claims.user_id)
        try:
            invoices = state.saas.billing.list_invoices(org.stripe_customer_id)
        except Exception as exc:  # noqa: BLE001 - Stripe trouble must not break the billing page
            observability.capture_exception(exc, operation="billing.invoices")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Billing history could not be loaded from Stripe.") from exc
        if invoices is None:
            return {"available": False, "invoices": []}
        return {"available": True, "invoices": invoices}

    @app.get("/admin/ingestion/failures", tags=["admin"])
    def ingestion_failures(claims: JwtClaims = Depends(require_roles("/admin/ingestion"))) -> dict:
        rows = state.documents.failures(claims.tenant_id)
        return {"failures": [
            {"document": d.to_dict(), "job": job.to_dict() if job else None} for d, job in rows
        ]}

    @app.post("/admin/ingestion/failures/{document_id}/retry", status_code=202, tags=["admin"])
    def retry_ingestion(document_id: str, claims: JwtClaims = Depends(require_roles("/admin/ingestion"))) -> dict:
        job = _documents_call(state.documents.reprocess, claims.tenant_id, claims.user_id, claims.roles, document_id)
        return {"job": job.to_dict()}

    @app.get("/entities/{document_id}", tags=["documents"])
    def entities(document_id: str, claims: JwtClaims = Depends(require_roles("/entities/"))) -> dict:
        known = _document_exists(claims.tenant_id, document_id) or (
            state.vector_store is not None
            and any(c.document_id == document_id for c in state.vector_store.for_tenant(claims.tenant_id))
        )
        if not known:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
        try:
            records = state.entity_service.get_records(claims.tenant_id, document_id)
            items = [{"id": r.id, "label": r.label, "value": r.value, "confidence": r.confidence}
                     for r in records]
        except Exception as exc:
            observability.capture_exception(exc, operation="api.entities")
            items = []
        return {"document_id": document_id, "entities": items}

    # ── Stripe webhook (unauthenticated, signature-verified) ─────────────
    @app.post("/billing/webhook", tags=["billing"])
    async def webhook(request: Request) -> dict:
        payload = await request.body()
        sig = request.headers.get("Stripe-Signature", "")
        billing = state.saas.billing
        if not getattr(billing, "supports_checkout", False):
            return {"received": True, "note": "demo billing — ignored"}
        try:
            event = billing.verify_webhook(payload, sig)
        except Exception as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Webhook verification failed: {exc}") from exc
        try:
            outcome = state.saas.apply_webhook_event(event)
        except Exception as exc:
            observability.capture_exception(exc, operation="api.webhook")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Webhook processing error.") from exc
        # 200 for duplicates and stale events too, so Stripe stops resending them.
        return {"received": True, "outcome": outcome}

    return app


def _all_docs(tenant_id: str, ws_filter) -> list[dict]:
    """Every document of a tenant: the upload records, plus (in the in-memory
    demo) chunks that were loaded directly by scripts or tests without one."""
    try:
        docs = [d.to_dict() for d in state.documents.list(tenant_id)]
    except Exception as exc:  # noqa: BLE001 - a listing must degrade, not fail the page
        observability.capture_exception(exc, operation="api.list_documents")
        docs = []
    if state.vector_store is not None:
        known = {d["document_id"] for d in docs}
        seeded: dict[str, dict] = {}
        for chunk in state.vector_store.for_tenant(tenant_id):
            if chunk.document_id in known:
                continue
            d = seeded.setdefault(chunk.document_id, {
                "document_id": chunk.document_id, "id": chunk.document_id, "title": chunk.title,
                "source_kind": chunk.source_type, "chunk_count": 0, "status": "ready"})
            d["chunk_count"] += 1
        docs.extend(seeded.values())
    if ws_filter is not None:
        docs = [d for d in docs if d["document_id"] in ws_filter]
    return docs


app = create_app()
