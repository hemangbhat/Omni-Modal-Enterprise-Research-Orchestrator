"""File storage adapter: local filesystem by default, S3-compatible if enabled.

The local adapter is the real, default path used by the demo. The S3 adapter is
only selected when ``S3_BUCKET`` is set AND ``boto3`` is importable; otherwise
the system logs why and falls back to local storage (never crashes).

Files move through storage as files, never as one big ``bytes`` value, so a
50 MB upload does not need 50 MB (or several copies of it) in memory.
Browser uploads go to S3 through a presigned POST whose policy pins the exact
size, so a client cannot store more than it announced.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    backend: str
    location: str  # absolute path (local) or s3://bucket/key


def safe_download_name(file_name: str) -> str:
    """A file name that cannot break out of a Content-Disposition header."""
    cleaned = "".join(ch for ch in file_name if ch.isprintable() and ch not in '"\\;\r\n')
    return cleaned.strip() or "download"


def content_disposition(file_name: str) -> str:
    """An attachment header that works for any file name: an ASCII fallback plus
    the exact UTF-8 name (RFC 6266), since headers themselves must be latin-1."""
    from urllib.parse import quote  # noqa: PLC0415

    name = safe_download_name(file_name)
    ascii_name = name.encode("ascii", "ignore").decode().strip() or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


class StorageAdapter(Protocol):
    backend: str

    def put(self, key: str, data: bytes) -> StoredObject: ...
    def put_file(self, key: str, source: Path) -> StoredObject: ...
    def get(self, key: str) -> bytes: ...
    def get_to_file(self, key: str, destination: Path) -> None: ...
    def move(self, source_key: str, destination_key: str) -> None: ...
    def delete(self, key: str) -> bool: ...
    def head(self, key: str) -> int | None: ...
    def presigned_put_url(self, key: str, expires_seconds: int = 900) -> str | None: ...
    def presigned_upload(self, key: str, size_bytes: int, content_type: str,
                         expires_seconds: int = 600) -> dict | None: ...
    def presigned_get_url(self, key: str, file_name: str, expires_seconds: int = 300) -> str | None: ...


class LocalStorageAdapter:
    """Filesystem-backed storage under a base directory."""

    backend = "local"

    def __init__(self, base_dir: str | None = None) -> None:
        self._base = Path(
            base_dir
            or os.environ.get("LOCAL_STORAGE_DIR")
            or (Path(tempfile.gettempdir()) / "omni_modal_storage")
        )
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

    def put_file(self, key: str, source: Path) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        return StoredObject(key=key, size_bytes=path.stat().st_size, backend=self.backend, location=str(path))

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def get_to_file(self, key: str, destination: Path) -> None:
        shutil.copyfile(self._path(key), destination)

    def move(self, source_key: str, destination_key: str) -> None:
        destination = self._path(destination_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self._path(source_key), destination)

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def head(self, key: str) -> int | None:
        """Size in bytes, or None when nothing is stored under ``key``."""
        path = self._path(key)
        return path.stat().st_size if path.is_file() else None

    def path_for(self, key: str) -> Path:
        """Where the bytes live, so the API can stream them without loading them."""
        return self._path(key)

    def presigned_put_url(self, key: str, expires_seconds: int = 900) -> str | None:
        # No signing authority offline: the API's own authenticated route is used instead.
        return None

    def presigned_upload(self, key: str, size_bytes: int, content_type: str,
                         expires_seconds: int = 600) -> dict | None:
        return None

    def presigned_get_url(self, key: str, file_name: str, expires_seconds: int = 300) -> str | None:
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

    def put_file(self, key: str, source: Path) -> StoredObject:
        self._client.upload_file(str(source), self._bucket, key)
        return StoredObject(
            key=key, size_bytes=source.stat().st_size, backend=self.backend,
            location=f"s3://{self._bucket}/{key}",
        )

    def get(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def get_to_file(self, key: str, destination: Path) -> None:
        self._client.download_file(self._bucket, key, str(destination))

    def move(self, source_key: str, destination_key: str) -> None:
        self._client.copy_object(
            Bucket=self._bucket, Key=destination_key,
            CopySource={"Bucket": self._bucket, "Key": source_key},
        )
        self._client.delete_object(Bucket=self._bucket, Key=source_key)

    def delete(self, key: str) -> bool:
        self._client.delete_object(Bucket=self._bucket, Key=key)
        return True

    def head(self, key: str) -> int | None:
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # botocore ClientError 404 / NoSuchKey
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return int(resp.get("ContentLength", 0))

    def presigned_put_url(self, key: str, expires_seconds: int = 900) -> str | None:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )

    def presigned_upload(self, key: str, size_bytes: int, content_type: str,
                         expires_seconds: int = 600) -> dict | None:
        """A browser form upload whose policy accepts exactly ``size_bytes`` bytes."""
        post = self._client.generate_presigned_post(
            Bucket=self._bucket, Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", size_bytes, size_bytes]],
            ExpiresIn=expires_seconds,
        )
        return {"url": post["url"], "fields": post["fields"]}

    def presigned_get_url(self, key: str, file_name: str, expires_seconds: int = 300) -> str | None:
        safe_name = safe_download_name(file_name)
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{safe_name}"',
            },
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
