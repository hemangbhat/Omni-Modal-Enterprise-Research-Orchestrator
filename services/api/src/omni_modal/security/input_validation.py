from __future__ import annotations
import re

UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

MAX_BODY_BYTES      = 1_048_576   # 1 MiB
MAX_QUERY_CHARS     = 4_096
MAX_TENANT_ID_CHARS = 128


class ValidationError(ValueError):
    """Raised when an HTTP request fails structural validation."""


def assert_body_size(content_length: int) -> None:
    if content_length > MAX_BODY_BYTES:
        raise ValidationError(
            f"Request body exceeds maximum allowed size of {MAX_BODY_BYTES} bytes."
        )


def assert_query_length(query: str) -> None:
    if len(query) > MAX_QUERY_CHARS:
        raise ValidationError(
            f"query field must not exceed {MAX_QUERY_CHARS} characters."
        )


def assert_tenant_id(tenant_id: object) -> str:
    if not isinstance(tenant_id, str) or not tenant_id or len(tenant_id) > MAX_TENANT_ID_CHARS:
        raise ValidationError(
            "tenant_id must be a non-empty string of at most "
            f"{MAX_TENANT_ID_CHARS} characters."
        )
    return tenant_id


def assert_document_id_uuid(document_id: object) -> str:
    if not isinstance(document_id, str):
        raise ValidationError("document_id must be a string.")
    if not UUID_V4_RE.match(document_id):
        raise ValidationError("document_id must be a valid UUID v4.")
    return document_id
