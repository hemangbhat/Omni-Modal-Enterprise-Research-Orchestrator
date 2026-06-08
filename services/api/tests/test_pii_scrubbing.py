"""Property-based tests for PII scrubbing completeness.

Feature: observability-and-recovery
Property 1: PII Scrubbing Completeness

Validates: Requirements 2.5, 6.5
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# Ensure the src package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hypothesis import given, settings, strategies as st

from omni_modal.observability import scrub_value, scrub_pii


# ---------------------------------------------------------------------------
# Compiled patterns used to DETECT PII in a string (same rules as the SUT)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_URL_RE = re.compile(r"[a-zA-Z]+://[^\s]+")
_SECRET_RE = re.compile(r"(?:password|secret|token|key)\s*[:=]\s*\S+", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Hypothesis strategies for generating PII-bearing strings
# ---------------------------------------------------------------------------

# Valid local parts for emails: one or more alphanumeric chars
_email_local = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=20,
)
_domain_label = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=10,
)

_email_strategy = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    local=_email_local,
    domain=_domain_label,
    tld=st.sampled_from(["com", "org", "net", "io", "co"]),
)

_conn_string_strategy = st.builds(
    lambda user, password, host, db: f"postgres://{user}:{password}@{host}/{db}",
    user=_domain_label,
    password=_domain_label,
    host=_domain_label,
    db=_domain_label,
)

_secret_kv_strategy = st.builds(
    lambda keyword, sep, value: f"{keyword}{sep}{value}",
    keyword=st.sampled_from(["password", "secret", "token", "key", "PASSWORD", "Secret"]),
    sep=st.sampled_from(["=", ": ", "="]),
    value=_domain_label,
)

# Plain text that contains NO PII (no @, no ://, no secret keywords)
_plain_text_strategy = st.text(
    alphabet=st.characters(
        blacklist_characters="@:/",
        blacklist_categories=("Cs",),  # no surrogates
    ),
    min_size=0,
    max_size=200,
).filter(
    lambda s: not _EMAIL_RE.search(s)
    and not _URL_RE.search(s)
    and not _SECRET_RE.search(s)
)


class TestScrubValuePIICompleteness(unittest.TestCase):
    """Property 1 – PII Scrubbing Completeness for scrub_value()."""

    # ------------------------------------------------------------------
    # Property: scrub_value removes email addresses
    # ------------------------------------------------------------------

    @given(
        prefix=st.text(max_size=30),
        email=_email_strategy,
        suffix=st.text(max_size=30),
    )
    @settings(max_examples=100)
    def test_email_is_scrubbed(self, prefix: str, email: str, suffix: str) -> None:
        """**Validates: Requirements 2.5, 6.5** — emails are removed from output."""
        combined = f"{prefix}{email}{suffix}"
        result = scrub_value(combined)
        self.assertFalse(
            _EMAIL_RE.search(result),
            f"Email {email!r} still present after scrubbing: {result!r}",
        )

    # ------------------------------------------------------------------
    # Property: scrub_value removes connection strings / URLs
    # ------------------------------------------------------------------

    @given(
        prefix=st.text(max_size=30),
        conn=_conn_string_strategy,
        suffix=st.text(max_size=30),
    )
    @settings(max_examples=100)
    def test_connection_string_is_scrubbed(
        self, prefix: str, conn: str, suffix: str
    ) -> None:
        """**Validates: Requirements 2.5, 6.5** — connection strings are removed."""
        combined = f"{prefix}{conn}{suffix}"
        result = scrub_value(combined)
        self.assertFalse(
            _URL_RE.search(result),
            f"Connection string still present after scrubbing: {result!r}",
        )

    # ------------------------------------------------------------------
    # Property: scrub_value removes secret key-value pairs
    # ------------------------------------------------------------------

    @given(
        prefix=st.text(max_size=30),
        kv=_secret_kv_strategy,
        suffix=st.text(max_size=30),
    )
    @settings(max_examples=100)
    def test_secret_kv_is_scrubbed(self, prefix: str, kv: str, suffix: str) -> None:
        """**Validates: Requirements 2.5, 6.5** — secret key-value pairs are removed."""
        combined = f"{prefix}{kv}{suffix}"
        result = scrub_value(combined)
        self.assertFalse(
            _SECRET_RE.search(result),
            f"Secret KV {kv!r} still present after scrubbing: {result!r}",
        )

    # ------------------------------------------------------------------
    # Property: scrub_value leaves plain (non-PII) strings unchanged
    # ------------------------------------------------------------------

    @given(plain=_plain_text_strategy)
    @settings(max_examples=100)
    def test_plain_string_unchanged(self, plain: str) -> None:
        """**Validates: Requirements 2.5, 6.5** — non-PII strings pass through unchanged."""
        self.assertEqual(
            scrub_value(plain),
            plain,
            f"Plain string {plain!r} was unexpectedly modified.",
        )


class TestScrubPIIDict(unittest.TestCase):
    """Property 1 – PII Scrubbing Completeness for scrub_pii()."""

    @given(
        keys=st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=5, unique=True),
        emails=st.lists(_email_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_dict_email_values_are_scrubbed(
        self, keys: list[str], emails: list[str]
    ) -> None:
        """**Validates: Requirements 2.5, 6.5** — dict values with emails are scrubbed."""
        data = {k: v for k, v in zip(keys, emails)}
        result = scrub_pii(data)
        for v in result.values():
            self.assertFalse(
                _EMAIL_RE.search(str(v)),
                f"Email still present in scrubbed dict: {result!r}",
            )

    @given(
        keys=st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=5, unique=True),
        conns=st.lists(_conn_string_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_dict_connection_string_values_are_scrubbed(
        self, keys: list[str], conns: list[str]
    ) -> None:
        """**Validates: Requirements 2.5, 6.5** — dict values with connection strings are scrubbed."""
        data = {k: v for k, v in zip(keys, conns)}
        result = scrub_pii(data)
        for v in result.values():
            self.assertFalse(
                _URL_RE.search(str(v)),
                f"Connection string still present in scrubbed dict: {result!r}",
            )

    @given(
        keys=st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=5, unique=True),
        kvs=st.lists(_secret_kv_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_dict_secret_kv_values_are_scrubbed(
        self, keys: list[str], kvs: list[str]
    ) -> None:
        """**Validates: Requirements 2.5, 6.5** — dict values with secret KVs are scrubbed."""
        data = {k: v for k, v in zip(keys, kvs)}
        result = scrub_pii(data)
        for v in result.values():
            self.assertFalse(
                _SECRET_RE.search(str(v)),
                f"Secret KV still present in scrubbed dict: {result!r}",
            )

    def test_nested_dict_is_scrubbed(self) -> None:
        """**Validates: Requirements 2.5** — nested dicts are recursively scrubbed."""
        data = {
            "level1": {
                "email": "user@example.com",
                "conn": "postgres://user:pass@host/db",
            },
            "token": "token=supersecret123",
        }
        result = scrub_pii(data)
        self.assertFalse(_EMAIL_RE.search(str(result["level1"]["email"])))
        self.assertFalse(_URL_RE.search(str(result["level1"]["conn"])))
        self.assertFalse(_SECRET_RE.search(str(result["token"])))

    def test_non_string_values_unchanged(self) -> None:
        """Non-string, non-dict values are passed through without modification."""
        data = {"count": 42, "ratio": 3.14, "flag": True, "nothing": None}
        result = scrub_pii(data)
        self.assertEqual(result["count"], 42)
        self.assertEqual(result["ratio"], 3.14)
        self.assertEqual(result["flag"], True)
        self.assertIsNone(result["nothing"])


if __name__ == "__main__":
    unittest.main()
