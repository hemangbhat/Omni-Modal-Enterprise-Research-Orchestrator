"""FastAPI application surface (Phase D).

A production-grade ASGI app (async, Pydantic validation, OpenAPI docs) that
reuses the existing domain logic — auth accounts, the QA/ADK workflow, and the
SaaS service. This is the migration target away from the stdlib HTTP server;
the two share all business logic so behaviour stays identical.

Run with:  uvicorn omni_modal.api:app --port 8000
"""

from omni_modal.api.app import app, create_app

__all__ = ["app", "create_app"]
