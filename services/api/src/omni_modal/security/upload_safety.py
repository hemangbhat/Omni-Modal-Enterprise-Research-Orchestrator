from __future__ import annotations
from pathlib import Path

MAX_FILE_BYTES = 52_428_800  # 50 MiB

ALLOWED_MIME_TYPES = frozenset({
    "application/pdf",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",
    "audio/flac",
    "audio/x-flac",
    "audio/ogg",
    "audio/webm",
})

EXTENSION_TO_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".mp3":  "audio/mpeg",
    ".wav":  "audio/wav",
    ".m4a":  "audio/mp4",
    ".flac": "audio/flac",
    ".ogg":  "audio/ogg",
    ".webm": "audio/webm",
}


class UploadSafetyError(Exception):
    """Raised when a file fails pre-ingestion safety checks."""
    def __init__(self, message: str, file_size: int, detected_mime: str | None) -> None:
        super().__init__(message)
        self.file_size = file_size
        self.detected_mime = detected_mime


def sniff_mime_type(file_path: Path) -> str | None:
    """Return the content-sniffed MIME type using python-magic or filetype fallback.
    Returns None if neither library is installed."""
    try:
        import magic  # python-magic
        return magic.from_file(str(file_path), mime=True)
    except ImportError:
        pass
    try:
        import filetype  # type: ignore[import]
        kind = filetype.guess(str(file_path))
        return kind.mime if kind else None
    except ImportError:
        return None


def assert_upload_safe(file_path: Path) -> tuple[int, str | None]:
    """Check size and MIME type. Returns (file_size_bytes, detected_mime).

    Raises UploadSafetyError on violation.
    """
    file_size = file_path.stat().st_size if file_path.exists() else 0

    if file_size > MAX_FILE_BYTES:
        raise UploadSafetyError(
            f"File size {file_size} exceeds maximum {MAX_FILE_BYTES} bytes.",
            file_size=file_size,
            detected_mime=None,
        )

    detected_mime = sniff_mime_type(file_path)
    extension_expected = EXTENSION_TO_MIME.get(file_path.suffix.lower())

    if detected_mime and detected_mime not in ALLOWED_MIME_TYPES:
        raise UploadSafetyError(
            f"Detected MIME type '{detected_mime}' is not permitted.",
            file_size=file_size,
            detected_mime=detected_mime,
        )

    if detected_mime and extension_expected and detected_mime != extension_expected:
        raise UploadSafetyError(
            f"MIME type mismatch: extension suggests '{extension_expected}' "
            f"but content is '{detected_mime}'.",
            file_size=file_size,
            detected_mime=detected_mime,
        )

    return file_size, detected_mime
