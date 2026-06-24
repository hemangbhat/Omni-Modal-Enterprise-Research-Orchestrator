"""Run the FastAPI app:  python -m omni_modal.api  (or: uvicorn omni_modal.api:app)."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn  # noqa: PLC0415

    uvicorn.run(
        "omni_modal.api:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("UVICORN_RELOAD", "false").lower() == "true",
    )


if __name__ == "__main__":
    main()
