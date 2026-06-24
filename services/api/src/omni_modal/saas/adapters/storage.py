"""File storage adapter: local filesystem by default, S3-compatible if enabled.

The local adapter is the real, default path used by the demo. The S3 adapter is
only selected when ``S3_BUCKET`` is set AND ``boto3`` is importable; otherwise
the system logs why and falls back to local storage (never crashes). Presigned
URL support is exposed on the interface; the local adapter returns a
file:// style reference since there is no signing authority offline.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    backend: str
    location: str  # absolute path (local) or s3://bucket/key


class StorageAdapter(Protocol):
    backend: str

    def put(self, key: str, data: bytes) -> StoredObject: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> bool: ...
    def presigned_put_url(self, key: str, expires_seconds: int = 900) -> str | None: ...


class LocalStorageAdapter:
    """Filesystem-backed storage under a base directory."""

    backend = "local"

    def __init__(self, base_dir: str | None = None) -> None:
        self._base = Path(base_dir or (Path(tempfile.gettempdir()) / "omni_modal_storage"))
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Prevent path traversal: collapse the key to a safe relative name.
        safe = key.replace("..", "_").lstrip("/\\")
        path = (self._base / safe).resolve()
        if self._base.resolve() not in path.parents and path != self._base.resolve():
            raise ValueError("Invalid storage key (path traversal).")
        return path

    def put(self, key: str, data: bytes) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(key=key, size_bytes=len(data), backend=self.backend, location=str(path))

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def presigned_put_url(self, key: str, expires_seconds: int = 900) -> str | None:
        # No signing authority offline; return a local reference for transparency.
        return None


class S3StorageAdapter:
    """S3-compatible storage. Requires boto3 and S3_BUCKET; activated only when set."""

    backend = "s3"

    def __init__(self) -> None:
        import boto3  # type: ignore[import-not-found]  # noqa: PLC0415

        self._bucket = os.environ["S3_BUCKET"]
        kwargs: dict[str, object] = {}
        if os.environ.get("S3_ENDPOINT_URL"):
            kwargs["endpoint_url"] = os.environ["S3_ENDPOINT_URL"]
        if os.environ.get("S3_REGION"):
            kwargs["region_name"] = os.environ["S3_REGION"]
        self._client = boto3.client("s3", **kwargs)

    def put(self, key: str, data: bytes) -> StoredObject:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return StoredObject(
            key=key, size_bytes=len(data), backend=self.backend,
            location=f"s3://{self._bucket}/{key}",
        )

    def get(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def delete(self, key: str) -> bool:
        self._client.delete_object(Bucket=self._bucket, Key=key)
        return True

    def presigned_put_url(self, key: str, expires_seconds: int = 900) -> str | None:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


def select_storage_adapter() -> StorageAdapter:
    """Return the S3 adapter when configured and importable, else local."""
    if os.environ.get("S3_BUCKET"):
        try:
            return S3StorageAdapter()
        except Exception as exc:  # pragma: no cover - depends on boto3/env
            import sys  # noqa: PLC0415

            print(
                f"[storage] S3_BUCKET set but S3 adapter unavailable ({exc}); "
                f"falling back to local storage.",
                file=sys.stderr,
            )
    return LocalStorageAdapter()
