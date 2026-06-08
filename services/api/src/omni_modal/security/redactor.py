from __future__ import annotations
import hashlib
from dataclasses import replace

from omni_modal.orchestration.a2a import A2AResearchRequest

MAX_INTERNAL_STATUS_CHARS = 500


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _redact_chunk_content(text: str, chunk_fingerprints: frozenset[str]) -> str:
    for fp in chunk_fingerprints:
        if fp in text:
            text = text.replace(fp, "[REDACTED]")
    return text


class ContentLeakError(Exception):
    """Raised when a delegation payload contains internal content that cannot be redacted."""


def redact_request(
    request: A2AResearchRequest,
    chunk_texts: list[str],
) -> A2AResearchRequest:
    """Return a sanitised copy of request with internal content removed.

    - Truncates internal_status to MAX_INTERNAL_STATUS_CHARS.
    - Replaces chunk fingerprints in internal_status with [REDACTED].
    - Ensures question contains only the user's original question.

    Raises ContentLeakError if chunk text is detected in the question field.
    """
    fps = frozenset(_fingerprint(c) for c in chunk_texts if c)

    # Guard: question must not contain chunk content (check first 50 chars)
    for chunk_text in chunk_texts:
        if chunk_text and chunk_text[:50] in request.question:
            raise ContentLeakError(
                "Delegation question contains internal document content."
            )

    # Sanitise internal_status
    status = request.internal_status[:MAX_INTERNAL_STATUS_CHARS]
    status = _redact_chunk_content(status, fps)

    return replace(request, internal_status=status)
