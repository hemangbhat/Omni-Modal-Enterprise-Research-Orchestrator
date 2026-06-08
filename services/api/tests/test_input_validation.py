"""Tests for omni_modal.security.input_validation.

Covers Properties 8, 9, 10 (Hypothesis) and unit tests for all validator functions.
"""
from __future__ import annotations

import sys
import os
import re
import unittest

# ---------------------------------------------------------------------------
# Path bootstrap (mirrors other test files in this directory)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from omni_modal.security.input_validation import (
    MAX_BODY_BYTES,
    MAX_QUERY_CHARS,
    MAX_TENANT_ID_CHARS,
    UUID_V4_RE,
    ValidationError,
    assert_body_size,
    assert_document_id_uuid,
    assert_query_length,
    assert_tenant_id,
)


# ---------------------------------------------------------------------------
# Property 8: Query Length Validation Boundary
# Validates: Requirements 4.3
# ---------------------------------------------------------------------------

@given(s=st.text(min_size=0, max_size=MAX_QUERY_CHARS * 2))
@settings(max_examples=200)
def test_query_length_boundary(s):
    """Property 8: Query Length Validation Boundary — Validates: Requirements 4.3"""
    if len(s) > MAX_QUERY_CHARS:
        with pytest.raises(ValidationError):
            assert_query_length(s)
    else:
        assert_query_length(s)  # must not raise


# ---------------------------------------------------------------------------
# Property 9: UUID v4 Validation
# Validates: Requirements 4.5
# ---------------------------------------------------------------------------

@given(uid=st.uuids(version=4))
@settings(max_examples=100)
def test_valid_uuid_v4_passes(uid):
    """Property 9a: Valid UUID v4 always passes — Validates: Requirements 4.5"""
    result = assert_document_id_uuid(str(uid))
    assert result == str(uid)


@given(s=st.text().filter(lambda x: not UUID_V4_RE.match(x)))
@settings(max_examples=100)
def test_non_uuid_raises(s):
    """Property 9b: Non-UUID strings always fail — Validates: Requirements 4.5"""
    with pytest.raises(ValidationError):
        assert_document_id_uuid(s)


# ---------------------------------------------------------------------------
# Property 10: Tenant ID Validation
# Validates: Requirements 4.4
# ---------------------------------------------------------------------------

@given(v=st.one_of(st.text(min_size=0, max_size=256), st.integers(), st.none()))
@settings(max_examples=200)
def test_tenant_id_validation(v):
    """Property 10: Tenant ID Validation — Validates: Requirements 4.4"""
    if isinstance(v, str) and 1 <= len(v) <= MAX_TENANT_ID_CHARS:
        result = assert_tenant_id(v)
        assert result == v
    else:
        with pytest.raises(ValidationError):
            assert_tenant_id(v)


# ---------------------------------------------------------------------------
# Unit tests (example-based) — Validates: Requirements 4.1, 4.3, 4.4, 4.5
# ---------------------------------------------------------------------------

class TestAssertBodySize(unittest.TestCase):
    def test_exact_limit_passes(self):
        """assert_body_size at exactly MAX_BODY_BYTES must not raise."""
        assert_body_size(MAX_BODY_BYTES)  # no error

    def test_one_over_limit_raises(self):
        """assert_body_size at MAX_BODY_BYTES + 1 must raise ValidationError."""
        with self.assertRaises(ValidationError):
            assert_body_size(MAX_BODY_BYTES + 1)

    def test_zero_passes(self):
        assert_body_size(0)

    def test_large_value_raises(self):
        with self.assertRaises(ValidationError):
            assert_body_size(MAX_BODY_BYTES * 2)


class TestAssertQueryLength(unittest.TestCase):
    def test_exact_limit_passes(self):
        """assert_query_length at exactly MAX_QUERY_CHARS must not raise."""
        assert_query_length("a" * MAX_QUERY_CHARS)  # no error

    def test_one_over_limit_raises(self):
        """assert_query_length at MAX_QUERY_CHARS + 1 must raise ValidationError."""
        with self.assertRaises(ValidationError):
            assert_query_length("a" * (MAX_QUERY_CHARS + 1))

    def test_empty_string_passes(self):
        assert_query_length("")

    def test_single_char_passes(self):
        assert_query_length("x")


class TestAssertTenantId(unittest.TestCase):
    def test_valid_tenant_returns_value(self):
        """assert_tenant_id with a valid string returns the value unchanged."""
        result = assert_tenant_id("valid-tenant")
        self.assertEqual(result, "valid-tenant")

    def test_empty_string_raises(self):
        with self.assertRaises(ValidationError):
            assert_tenant_id("")

    def test_exactly_128_chars_passes(self):
        """assert_tenant_id with 128 characters must not raise."""
        assert_tenant_id("a" * 128)  # no error

    def test_129_chars_raises(self):
        """assert_tenant_id with 129 characters must raise ValidationError."""
        with self.assertRaises(ValidationError):
            assert_tenant_id("a" * 129)

    def test_integer_raises(self):
        with self.assertRaises(ValidationError):
            assert_tenant_id(42)  # type: ignore[arg-type]

    def test_none_raises(self):
        with self.assertRaises(ValidationError):
            assert_tenant_id(None)  # type: ignore[arg-type]

    def test_list_raises(self):
        with self.assertRaises(ValidationError):
            assert_tenant_id(["tenant"])  # type: ignore[arg-type]


class TestAssertDocumentIdUuid(unittest.TestCase):
    # A known valid UUID v4
    VALID_UUID_V4 = "550e8400-e29b-41d4-a716-446655440000"

    def test_valid_uuid_v4_passes(self):
        """A valid UUID v4 string must be returned unchanged."""
        result = assert_document_id_uuid(self.VALID_UUID_V4)
        self.assertEqual(result, self.VALID_UUID_V4)

    def test_non_string_raises(self):
        with self.assertRaises(ValidationError):
            assert_document_id_uuid(12345)  # type: ignore[arg-type]

    def test_empty_string_raises(self):
        with self.assertRaises(ValidationError):
            assert_document_id_uuid("")

    def test_random_string_raises(self):
        with self.assertRaises(ValidationError):
            assert_document_id_uuid("not-a-uuid")

    def test_uuid_v1_format_raises(self):
        """UUID v1-style value (version digit != 4) must be rejected."""
        uuid_v1 = "550e8400-e29b-11d4-a716-446655440000"  # version 1
        with self.assertRaises(ValidationError):
            assert_document_id_uuid(uuid_v1)

    def test_uppercase_uuid_v4_passes(self):
        """UUID v4 in uppercase must pass (regex is case-insensitive)."""
        upper = self.VALID_UUID_V4.upper()
        result = assert_document_id_uuid(upper)
        self.assertEqual(result, upper)

    def test_none_raises(self):
        with self.assertRaises(ValidationError):
            assert_document_id_uuid(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
