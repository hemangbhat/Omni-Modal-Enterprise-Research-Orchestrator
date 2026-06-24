"""Minimal, dependency-free ``.env`` loader.

The backend reads all of its configuration from ``os.environ`` (database URL,
embedding backend, JWT secret, Whisper/NER model paths, Sentry DSN, …). The
repository ships a fully-populated ``.env`` at its root, but Python does not
load that file automatically. Without this loader, running
``python -m omni_modal.main`` silently ignores ``.env`` and falls back to the
in-memory + hashing demo path — even when a real Neon database and semantic
embedding backend are configured.

This module locates the nearest ``.env`` (walking up from the current working
directory and from this file's location) and loads any keys that are not
already present in the environment. Process environment variables always win,
so explicit ``$env:FOO=...`` / ``export FOO=...`` overrides keep working.

No third-party dependency (e.g. python-dotenv) is required — the parser is a
small, well-tested subset that handles the formats used in this project:

    KEY=value
    KEY="quoted value"
    KEY='single quoted'
    export KEY=value      # leading `export ` is ignored
    # comments and blank lines are skipped
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["find_dotenv", "load_dotenv"]


def find_dotenv(filename: str = ".env") -> Path | None:
    """Return the path to the nearest ``.env`` file, or ``None``.

    Searches, in order:
      1. the current working directory and each of its parents, then
      2. this file's directory and each of its parents (covers the case where
         the process is launched from inside ``services/api``).

    The first existing match wins.
    """
    search_roots: list[Path] = []
    try:
        search_roots.append(Path.cwd())
    except OSError:  # pragma: no cover - cwd unavailable is rare
        pass
    search_roots.append(Path(__file__).resolve().parent)

    seen: set[Path] = set()
    for root in search_roots:
        for directory in (root, *root.parents):
            if directory in seen:
                continue
            seen.add(directory)
            candidate = directory / filename
            if candidate.is_file():
                return candidate
    return None


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_dotenv(
    path: str | os.PathLike[str] | None = None,
    *,
    override: bool = False,
) -> int:
    """Load variables from a ``.env`` file into ``os.environ``.

    Args:
        path: Explicit path to the env file. When ``None``, the nearest
            ``.env`` is discovered via :func:`find_dotenv`.
        override: When ``False`` (default), existing environment variables are
            left untouched so explicit overrides win. When ``True``, values
            from the file replace existing ones.

    Returns:
        The number of variables applied to ``os.environ``.
    """
    env_path = Path(path) if path is not None else find_dotenv()
    if env_path is None or not env_path.is_file():
        return 0

    applied = 0
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        return 0

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            continue
        value = _strip_quotes(value.strip())
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied += 1
    return applied
