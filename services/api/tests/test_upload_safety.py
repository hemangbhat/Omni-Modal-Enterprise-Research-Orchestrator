"""Tests for the Upload Safety Guard (security/upload_safety.py).

**Validates: Requirements 5.1, 5.2**
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import _path  # noqa: F401
import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.security.upload_safety import (
    ALLOWED_MIME_TYPES,
    EXTENSION_TO_MIME,
    MAX_FILE_BYTES,
    UploadSafetyError,
    assert_upload_safe,
    sniff_mime_type,
)


# ---------------------------------------------------------------------------
# 8.1  Property 16: Upload Safety Rejects Oversized or Disallowed MIME Files
# ---------------------------------------------------------------------------

_ALL_MIME = list(ALLOWED_MIME_TYPES) + [
    "text/plain",
    "image/jpeg",
    "application/exe",
    "application/octet-stream",
]


@given(
    file_size=st.integers(min_value=0, max_value=MAX_FILE_BYTES * 2),
    mime_type=st.sampled_from(_ALL_MIME),
)
@settings(max_examples=100)
def test_upload_safety_size_and_mime(file_size: int, mime_type: str) -> None:
    """Property 16: Upload Safety Rejects Oversized or Disallowed MIME Files
    Validates: Requirements 5.1, 5.2"""
    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = True
    fake_path.stat.return_value.st_size = file_size
    fake_path.suffix = ".pdf"  # use consistent extension

    with patch("omni_modal.security.upload_safety.sniff_mime_type", return_value=mime_type):
        if file_size > MAX_FILE_BYTES:
            with pytest.raises(UploadSafetyError) as exc_info:
                assert_upload_safe(fake_path)
            assert exc_info.value.file_size == file_size
        elif mime_type not in ALLOWED_MIME_TYPES:
            with pytest.raises(UploadSafetyError):
                assert_upload_safe(fake_path)
        elif mime_type == "application/pdf":  # matches .pdf extension
            size, detected = assert_upload_safe(fake_path)
            assert size == file_size
            assert detected == mime_type


# ---------------------------------------------------------------------------
# 8.2  Unit tests
# ---------------------------------------------------------------------------


def _make_fake_path(
    suffix: str = ".pdf",
    file_size: int = 1024,
    exists: bool = True,
) -> MagicMock:
    """Helper: create a mock Path with the given properties."""
    fake = MagicMock(spec=Path)
    fake.exists.return_value = exists
    fake.stat.return_value.st_size = file_size
    fake.suffix = suffix
    return fake


class TestUploadSafetyUnit(unittest.TestCase):

    # -- size boundary tests -------------------------------------------------

    def test_file_exactly_at_max_size_passes_when_mime_allowed(self) -> None:
        """File exactly at 50 MiB passes when MIME is allowed."""
        fake_path = _make_fake_path(suffix=".pdf", file_size=MAX_FILE_BYTES)
        with patch(
            "omni_modal.security.upload_safety.sniff_mime_type",
            return_value="application/pdf",
        ):
            size, mime = assert_upload_safe(fake_path)
        self.assertEqual(size, MAX_FILE_BYTES)
        self.assertEqual(mime, "application/pdf")

    def test_file_one_byte_over_max_raises_with_file_size(self) -> None:
        """File at 50 MiB + 1 byte raises UploadSafetyError with file_size set."""
        oversized = MAX_FILE_BYTES + 1
        fake_path = _make_fake_path(suffix=".pdf", file_size=oversized)
        with self.assertRaises(UploadSafetyError) as ctx:
            assert_upload_safe(fake_path)
        self.assertEqual(ctx.exception.file_size, oversized)
        self.assertIsNone(ctx.exception.detected_mime)

    # -- MIME type tests -----------------------------------------------------

    def test_mime_mismatch_pdf_extension_audio_mime_raises(self) -> None:
        """Extension .pdf but MIME audio/mpeg raises UploadSafetyError."""
        fake_path = _make_fake_path(suffix=".pdf", file_size=1024)
        with patch(
            "omni_modal.security.upload_safety.sniff_mime_type",
            return_value="audio/mpeg",
        ):
            with self.assertRaises(UploadSafetyError) as ctx:
                assert_upload_safe(fake_path)
        self.assertEqual(ctx.exception.detected_mime, "audio/mpeg")

    def test_disallowed_mime_type_raises(self) -> None:
        """A disallowed MIME type (text/plain) raises UploadSafetyError."""
        fake_path = _make_fake_path(suffix=".pdf", file_size=1024)
        with patch(
            "omni_modal.security.upload_safety.sniff_mime_type",
            return_value="text/plain",
        ):
            with self.assertRaises(UploadSafetyError) as ctx:
                assert_upload_safe(fake_path)
        self.assertEqual(ctx.exception.detected_mime, "text/plain")
        self.assertEqual(ctx.exception.file_size, 1024)

    def test_sniff_returns_none_passes_within_size_limit(self) -> None:
        """When sniff_mime_type returns None (no library), the check still passes
        as long as the file is within the size limit."""
        fake_path = _make_fake_path(suffix=".pdf", file_size=1024)
        with patch(
            "omni_modal.security.upload_safety.sniff_mime_type",
            return_value=None,
        ):
            size, mime = assert_upload_safe(fake_path)
        self.assertEqual(size, 1024)
        self.assertIsNone(mime)

    # -- Extension / MIME consistency for all 7 allowed extensions -----------

    def test_all_extensions_accepted_with_matching_mime(self) -> None:
        """All 7 extensions are accepted when the MIME matches the extension."""
        for ext, expected_mime in EXTENSION_TO_MIME.items():
            with self.subTest(ext=ext, mime=expected_mime):
                fake_path = _make_fake_path(suffix=ext, file_size=1024)
                with patch(
                    "omni_modal.security.upload_safety.sniff_mime_type",
                    return_value=expected_mime,
                ):
                    size, detected = assert_upload_safe(fake_path)
                self.assertEqual(size, 1024)
                self.assertEqual(detected, expected_mime)

    # -- sniff_mime_type fallback behaviour ----------------------------------

    def test_sniff_mime_type_returns_none_when_both_libraries_absent(self) -> None:
        """sniff_mime_type returns None when both magic and filetype are unavailable."""
        import builtins
        real_import = builtins.__import__

        def blocked_import(name: str, *args, **kwargs):  # type: ignore[override]
            if name in ("magic", "filetype"):
                raise ImportError(f"Simulated missing library: {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=blocked_import):
            result = sniff_mime_type(Path("fake_file.pdf"))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
