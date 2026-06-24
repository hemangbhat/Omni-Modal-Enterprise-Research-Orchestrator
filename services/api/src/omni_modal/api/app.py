"""FastAPI app: async endpoints, Pydantic validation, OpenAPI, reusing the
existing domain services so behaviour matches the stdlib server exactly."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from omni_modal.entity_extraction.service import EntityExtractionService
from omni_modal.ingestion import MultimodalIngestionPipeline
from omni_modal.ingestion.async_queue import AsyncIngestionQueue
from omni_modal.ingestion.extractors import LocalWhisperTranscriber
from omni_modal.ingestion.http_contract import (
    IngestionContractError,
    ingestion_request_from_payload,
)
from omni_modal.observability import observability
from omni_modal.orchestration import InternalResearchAdkWorkflow, Phase1Orchestrator
from omni_modal.orchestration.a2a import external_client_from_environment
from omni_modal.qa import (
    PgVectorChunkRetriever,
    QueryRequest,
    select_answer_synthesizer,
    select_embedding_provider,
    stream_markdown,
)
from omni_modal.qa.in_memory_store import (
    InMemoryChunkPersistence,
    InMemoryChunkRetriever,
    InMemoryVectorStore,
)
from omni_modal.saas import get_saas_service
from omni_modal.saas.plans import PLANS, PlanLimitExceeded
from omni_modal.security.accounts import AccountError, get_account_service
from omni_modal.security.auth import AuthError, JwtClaims, _make_jwt, jwt_secret_from_env, verify_jwt
from omni_modal.security.rbac import RbacError, assert_endpoint_roles


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


class QueryRequestModel(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(5, ge=1, le=50)
    min_similarity: float = Field(0.0, ge=0.0, le=1.0)


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
    filename: str
    content_base64: str
    source_kind: str | None = None
    title: str | None = None
    workspace_id: str | None = None


# ── App state / lifespan ─────────────────────────────────────────────────
class _State:
    workflow: Any = None
    saas: Any = None
    accounts: Any = None
    queue: Any = None
    entity_service: Any = None
    vector_store: Any = None


state = _State()


def _build_runtime() -> None:
    """Construct the data-plane components, mirroring the stdlib server so both
    share identical behaviour. Postgres path when DATABASE_URL is set, else the
    in-memory demo path."""
    selection = select_embedding_provider()
    provider = selection.provider
    whisper_model = os.environ.get("WHISPER_MODEL_PATH") or None
    audio = LocalWhisperTranscriber(model=whisper_model) if whisper_model else None
    state.entity_service = EntityExtractionService()

    if os.environ.get("DATABASE_URL"):
        from omni_modal.qa.pg_persistence import PostgresChunkPersistence  # noqa: PLC0415

        state.vector_store = None
        retriever = PgVectorChunkRetriever(provider)
        pipeline = MultimodalIngestionPipeline(
            audio_transcriber=audio,
            persistence=PostgresChunkPersistence(
                provider, database_url=os.environ.get("DATABASE_URL"),
                embedding_model=selection.backend,
                dimensions=getattr(provider, "dimensions", 384),
            ),
        )
    else:
        state.vector_store = InMemoryVectorStore()
        retriever = InMemoryChunkRetriever(provider, state.vector_store)
        pipeline = MultimodalIngestionPipeline(
            audio_transcriber=audio,
            persistence=InMemoryChunkPersistence(state.vector_store, provider),
        )

    state.queue = AsyncIngestionQueue(pipeline)
    state.queue.start_worker()
    state.workflow = InternalResearchAdkWorkflow(
        retriever, external_client_from_environment(), select_answer_synthesizer()
    )
    state.saas = get_saas_service()
    state.accounts = get_account_service()


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

    # ── Auth dependency ──────────────────────────────────────────────────
    def current_claims(authorization: str | None = Header(default=None)) -> JwtClaims:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
        token = authorization.split(" ", 1)[1].strip()
        try:
            return verify_jwt(token, jwt_secret_from_env())
        except AuthError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

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

    # ── Auth ─────────────────────────────────────────────────────────────
    def _issue(account) -> AuthResponse:
        exp = int(time.time()) + 7 * 24 * 3600
        token = _make_jwt(account.tenant_id, account.user_id, list(account.roles), exp,
                          jwt_secret_from_env())
        return AuthResponse(token=token, tenant_id=account.tenant_id, user_id=account.user_id,
                            roles=list(account.roles), email=account.email, expires_at=exp)

    @app.post("/auth/register", response_model=AuthResponse, status_code=201, tags=["auth"])
    async def register(body: RegisterRequest) -> AuthResponse:
        try:
            account = state.accounts.register(
                email=body.email, password=body.password, display_name=body.display_name
            )
        except AccountError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return _issue(account)

    @app.post("/auth/login", response_model=AuthResponse, tags=["auth"])
    async def login(body: LoginRequest) -> AuthResponse:
        account = state.accounts.authenticate(email=body.email, password=body.password)
        if account is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
        return _issue(account)

    # ── Query ────────────────────────────────────────────────────────────
    @app.post("/query", tags=["research"])
    async def query(body: QueryRequestModel, claims: JwtClaims = Depends(require_roles("/query"))) -> dict:
        meter(claims, "queries")
        req = QueryRequest(
            tenant_id=claims.tenant_id, user_id=claims.user_id, question=body.question,
            top_k=body.top_k, min_similarity=body.min_similarity,
        )
        try:
            result = state.workflow.answer(req)
        except Exception as exc:
            observability.capture_exception(exc, operation="api.query")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Query failed.") from exc
        return {**result.response.to_json_dict(), "trace": result.trace}

    @app.post("/query/stream", tags=["research"])
    async def query_stream(body: QueryRequestModel, claims: JwtClaims = Depends(require_roles("/query/stream"))):
        meter(claims, "queries")
        req = QueryRequest(
            tenant_id=claims.tenant_id, user_id=claims.user_id, question=body.question,
            top_k=body.top_k, min_similarity=body.min_similarity,
        )
        try:
            result = state.workflow.answer(req)
        except Exception as exc:
            observability.capture_exception(exc, operation="api.query_stream")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Query failed.") from exc

        final_payload = {**result.response.to_json_dict(), "trace": result.trace}

        def _events():
            for chunk in stream_markdown(result.response.answer_markdown):
                yield f"event: delta\ndata: {json.dumps({'delta': chunk})}\n\n"
            yield f"event: done\ndata: {json.dumps(final_payload)}\n\n"

        return StreamingResponse(_events(), media_type="text/event-stream")

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
    @app.post("/ingest/upload", status_code=202, tags=["ingestion"])
    async def ingest_upload(body: UploadRequest, claims: JwtClaims = Depends(require_roles("/ingest/upload"))) -> dict:
        meter(claims, "uploads")
        try:
            content = base64.b64decode(body.content_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "content_base64 is not valid base64.") from exc
        suffix = Path(body.filename).suffix
        upload_dir = Path(tempfile.gettempdir()) / "omni_modal_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        document_id = str(uuid.uuid4())
        dest = upload_dir / f"{document_id}{suffix}"
        dest.write_bytes(content)
        req = ingestion_request_from_payload({
            "tenant_id": claims.tenant_id, "document_id": document_id, "owner_id": claims.user_id,
            "file_path": str(dest), "source_kind": body.source_kind, "title": body.title or body.filename,
        })
        job = state.queue.enqueue(req)
        if body.workspace_id:
            state.saas.tag_document(document_id, body.workspace_id)
        state.saas.analytics.capture(event="upload", tenant_id=claims.tenant_id, user_id=claims.user_id,
                                     properties={"source_kind": body.source_kind or "pdf"})
        state.saas.notifications.add(
            tenant_id=claims.tenant_id, title="Upload received",
            body=f"'{body.title or body.filename}' is being processed.", kind="info", user_id=claims.user_id,
        )
        return {"job_id": job.id, "status": job.status, "document_id": document_id}

    @app.post("/ingest/local", status_code=202, tags=["ingestion"])
    async def ingest_local(request: Request, claims: JwtClaims = Depends(require_roles("/ingest/local"))) -> dict:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON body.") from exc
        payload = {**payload, "tenant_id": claims.tenant_id, "owner_id": claims.user_id}
        try:
            req = ingestion_request_from_payload(payload)
        except IngestionContractError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        job = state.queue.enqueue(req)
        return {"job_id": job.id, "status": job.status}

    @app.get("/ingest/jobs/{job_id}", tags=["ingestion"])
    async def job_status(job_id: str, claims: JwtClaims = Depends(current_claims)) -> dict:
        job = state.queue.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
        return {
            "job_id": job.id, "status": job.status,
            "document_id": job.request.document_id,
            "error_code": job.error_code.value if job.error_code else None,
            "error_message": job.error_message,
        }

    # ── Document views ───────────────────────────────────────────────────
    def _ws_filter(workspace_id: str | None):
        return state.saas.documents_in_workspace(workspace_id) if workspace_id else None

    @app.get("/documents", tags=["documents"])
    async def documents(workspace_id: str | None = None, claims: JwtClaims = Depends(require_roles("/documents"))) -> dict:
        wf = _ws_filter(workspace_id)
        docs: list[dict] = []
        if state.vector_store is not None:
            seen: dict[str, dict] = {}
            for chunk in state.vector_store.for_tenant(claims.tenant_id):
                if wf is not None and chunk.document_id not in wf:
                    continue
                d = seen.setdefault(chunk.document_id, {
                    "document_id": chunk.document_id, "title": chunk.title,
                    "source_kind": chunk.source_type, "chunk_count": 0, "status": "ready"})
                d["chunk_count"] += 1
            docs = list(seen.values())
        elif os.environ.get("DATABASE_URL"):
            docs = _db_documents(claims.tenant_id, wf)
        return {"documents": docs, "total": len(docs)}

    @app.get("/projects", tags=["documents"])
    async def projects(workspace_id: str | None = None, claims: JwtClaims = Depends(require_roles("/projects"))) -> dict:
        wf = _ws_filter(workspace_id)
        out: list[dict] = []
        rows = _all_docs(claims.tenant_id, wf)
        for d in rows:
            did = d["document_id"]
            out.append({
                "id": did, "code": f"PRJ-{did[:8].upper()}", "name": d["title"],
                "icon": "science" if d["source_kind"] == "audio" else "description",
                "status": "Active", "priority": "Medium", "source_kind": d["source_kind"],
                "chunk_count": d["chunk_count"], "docs": d["chunk_count"], "updated": "Recently",
            })
        return {"projects": out, "total": len(out)}

    @app.get("/archives", tags=["documents"])
    async def archives(workspace_id: str | None = None, claims: JwtClaims = Depends(require_roles("/archives"))) -> dict:
        wf = _ws_filter(workspace_id)
        out: list[dict] = []
        for d in _all_docs(claims.tenant_id, wf):
            did = d["document_id"]
            out.append({
                "id": did, "name": f"ARC-{did[:8].upper()}", "title": d["title"],
                "kind": f"{str(d['source_kind']).upper()} • AES-256", "classification": "Internal",
                "archived": "—", "accessed": "Recently", "expiry": "7 years", "size": "—",
                "status": "indexed",
            })
        return {"archives": out, "total": len(out)}

    @app.get("/entities/{document_id}", tags=["documents"])
    async def entities(document_id: str, claims: JwtClaims = Depends(require_roles("/entities/"))) -> dict:
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
            state.saas.apply_webhook_event(event)
        except Exception as exc:
            observability.capture_exception(exc, operation="api.webhook")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Webhook processing error.") from exc
        return {"received": True}

    return app


def _db_documents(tenant_id: str, ws_filter) -> list[dict]:
    try:
        import psycopg  # noqa: PLC0415
        from psycopg.rows import dict_row  # noqa: PLC0415

        with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
            rows = conn.execute(
                """SELECT d.id as document_id, d.title, d.source_type as source_kind, d.status,
                          COUNT(c.id) as chunk_count
                   FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id
                   WHERE d.tenant_id = %s
                   GROUP BY d.id, d.title, d.source_type, d.status
                   ORDER BY d.created_at DESC LIMIT 100""",
                (tenant_id,),
            ).fetchall()
        out = [{"document_id": str(r["document_id"]), "title": r["title"],
                "source_kind": r["source_kind"], "status": r["status"],
                "chunk_count": int(r["chunk_count"])} for r in rows]
        if ws_filter is not None:
            out = [d for d in out if d["document_id"] in ws_filter]
        return out
    except Exception as exc:
        observability.capture_exception(exc, operation="api.db_documents")
        return []


def _all_docs(tenant_id: str, ws_filter) -> list[dict]:
    """Unified document list across in-memory and DB paths (for project/archive views)."""
    if state.vector_store is not None:
        seen: dict[str, dict] = {}
        for chunk in state.vector_store.for_tenant(tenant_id):
            if ws_filter is not None and chunk.document_id not in ws_filter:
                continue
            d = seen.setdefault(chunk.document_id, {
                "document_id": chunk.document_id, "title": chunk.title,
                "source_kind": chunk.source_type, "chunk_count": 0})
            d["chunk_count"] += 1
        return list(seen.values())
    if os.environ.get("DATABASE_URL"):
        return _db_documents(tenant_id, ws_filter)
    return []


app = create_app()
