"""Run the FastAPI app:  python -m omni_modal.api  (or: uvicorn omni_modal.api:app)."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn  # noqa: PLC0415

    from omni_modal import env_loader  # noqa: PLC0415

    # Honour the repository .env when launched as the entry point, like the
    # stdlib reference server. Process environment variables still win. Done
    # here, not at import, so importing the package never changes os.environ.
    env_loader.load_dotenv()

    uvicorn.run(
        # Build the app after .env is loaded (factory), so settings read while
        # the app is constructed, such as CORS_ALLOW_ORIGINS, see those values.
        "omni_modal.api.app:create_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("UVICORN_RELOAD", "false").lower() == "true",
    )


if __name__ == "__main__":
    main()
