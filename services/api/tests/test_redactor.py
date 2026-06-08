from __future__ import annotations

import unittest

import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

import _path  # noqa: F401 — adds src/ to sys.path

from omni_modal.security.redactor import (
    ContentLeakError,
    MAX_INTERNAL_STATUS_CHARS,
    _fingerprint,
    redact_request,
)
from omni_modal.orchestration.a2a import A2AResearchRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    question: str = "What is the market outlook?",
    status: str = "Internal analysis complete.",
) -> A2AResearchRequest:
    return A2AResearchRequest(
        request_id="req-1",
        tenant_id="tenant-a",
        user_id="user-1",
        question=question,
        reason="research query",
        internal_status=status,
    )


# ---------------------------------------------------------------------------
# Property 11: Redactor Never Leaks Chunk Content in Internal Status
# ---------------------------------------------------------------------------

@given(chunk_texts=st.lists(st.text(min_size=16, max_size=100), min_size=0, max_size=5))
@settings(max_examples=100)
def test_no_chunk_leak_in_internal_status(chunk_texts):
    """Property 11: Redactor Never Leaks Chunk Content in Internal Status
    Validates: Requirements 6.1, 6.4"""
    # Build internal_status that contains chunk fingerprints (simulating embedding in status)
    status_with_fps = " ".join(_fingerprint(c) for c in chunk_texts if c) + " some context"
    req = _make_request(status=status_with_fps)

    result = redact_request(req, chunk_texts)

    # No verbatim chunk text substring of 16+ chars should appear in the result
    for chunk_text in chunk_texts:
        if len(chunk_text) >= 16:
            assert chunk_text[:16] not in result.internal_status


# ---------------------------------------------------------------------------
# Property 12: Redactor Always Truncates Internal Status
# ---------------------------------------------------------------------------

@given(internal_status=st.text(min_size=0, max_size=2000))
@settings(max_examples=100)
def test_truncation_invariant(internal_status):
    """Property 12: Redactor Always Truncates Internal Status to Maximum Length
    Validates: Requirements 6.4"""
    req = _make_request(status=internal_status)
    result = redact_request(req, [])
    assert len(result.internal_status) <= MAX_INTERNAL_STATUS_CHARS


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestRedactRequest(unittest.TestCase):

    def test_status_exactly_max_chars_unchanged(self):
        """internal_status exactly 500 chars is returned unchanged (no truncation)."""
        status = "x" * MAX_INTERNAL_STATUS_CHARS
        req = _make_request(status=status)
        result = redact_request(req, [])
        self.assertEqual(len(result.internal_status), MAX_INTERNAL_STATUS_CHARS)
        self.assertEqual(result.internal_status, status)

    def test_status_over_max_chars_truncated(self):
        """internal_status of 501 chars is truncated to exactly 500."""
        status = "y" * (MAX_INTERNAL_STATUS_CHARS + 1)
        req = _make_request(status=status)
        result = redact_request(req, [])
        self.assertEqual(len(result.internal_status), MAX_INTERNAL_STATUS_CHARS)

    def test_question_with_chunk_prefix_raises_content_leak_error(self):
        """Question containing first 50 chars of a chunk text raises ContentLeakError."""
        chunk = "A" * 60  # longer than 50 chars
        question = "Here is some internal data: " + chunk[:50] + " end"
        req = _make_request(question=question)
        with self.assertRaises(ContentLeakError):
            redact_request(req, [chunk])

    def test_chunk_fingerprint_in_status_is_redacted(self):
        """Fingerprint of a chunk embedded in internal_status is replaced with [REDACTED]."""
        chunk = "This is internal document content."
        fp = _fingerprint(chunk)
        status = f"Analysis: {fp} was found."
        req = _make_request(status=status)
        result = redact_request(req, [chunk])
        self.assertNotIn(fp, result.internal_status)
        self.assertIn("[REDACTED]", result.internal_status)

    def test_empty_chunk_texts_request_returned_truncated(self):
        """Empty chunk_texts list: request returned unchanged (status still truncated)."""
        status = "Normal status with no chunk references."
        req = _make_request(status=status)
        result = redact_request(req, [])
        # Status unchanged (it's under the limit)
        self.assertEqual(result.internal_status, status)
        # Other fields untouched
        self.assertEqual(result.question, req.question)
        self.assertEqual(result.request_id, req.request_id)
        self.assertEqual(result.tenant_id, req.tenant_id)

    def test_multiple_fingerprints_all_redacted(self):
        """Multiple chunk fingerprints all get replaced in internal_status."""
        chunks = ["First chunk content here.", "Second chunk content here."]
        fps = [_fingerprint(c) for c in chunks]
        status = f"Data: {fps[0]} and {fps[1]} processed."
        req = _make_request(status=status)
        result = redact_request(req, chunks)
        for fp in fps:
            self.assertNotIn(fp, result.internal_status)
        self.assertEqual(result.internal_status.count("[REDACTED]"), 2)

    def test_empty_chunk_text_not_checked_for_leak(self):
        """Empty string in chunk_texts is ignored (no error raised)."""
        req = _make_request(question="What is the outlook?")
        # Should not raise even though empty string would trivially be 'in' anything
        result = redact_request(req, ["", "  "])
        self.assertIsNotNone(result)

    def test_immutability_original_request_unchanged(self):
        """Original A2AResearchRequest is not mutated."""
        chunk = "sensitive data chunk"
        fp = _fingerprint(chunk)
        original_status = f"Status with {fp} embedded."
        req = _make_request(status=original_status)
        redact_request(req, [chunk])
        # Original must be unchanged
        self.assertEqual(req.internal_status, original_status)


if __name__ == "__main__":
    unittest.main()
