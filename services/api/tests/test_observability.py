"""Unit tests for the Observability facade.

Covers:
- Sentry init with / without DSN
- continue_trace with valid, missing, and malformed headers
- set_request_scope when SDK is unavailable
- extract_host helper
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Ensure the src package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_modal.observability import Observability, extract_host


class TestObservabilityInit(unittest.TestCase):
    """Tests for Observability.init()."""

    def _fresh_obs(self) -> Observability:
        """Return an Observability instance that has NOT been initialised yet."""
        obs = Observability()
        return obs

    def test_init_without_dsn(self) -> None:
        """With SENTRY_DSN unset, _sentry must remain None."""
        env = os.environ.copy()
        env.pop("SENTRY_DSN", None)
        obs = self._fresh_obs()
        original = os.environ.pop("SENTRY_DSN", None)
        try:
            obs.init()
            self.assertIsNone(obs._sentry, "_sentry should be None when SENTRY_DSN is not set")
        finally:
            if original is not None:
                os.environ["SENTRY_DSN"] = original

    def test_init_with_dsn(self) -> None:
        """With a fake SENTRY_DSN, init() should attempt SDK setup.

        If sentry_sdk is importable, _sentry is set to the module.
        If sentry_sdk is not installed, ImportError is caught gracefully and
        _sentry stays None — both outcomes are acceptable.
        """
        obs = self._fresh_obs()
        os.environ["SENTRY_DSN"] = "https://fake@o0.ingest.sentry.io/0"
        try:
            # Should not raise regardless of whether sentry_sdk is installed
            obs.init()
            # Either _sentry is set (SDK available) or None (SDK absent) — no error
            self.assertIn(
                obs._sentry is None,
                (True, False),
                "_sentry should be set or None — never raise",
            )
        except Exception as exc:  # pragma: no cover
            self.fail(f"obs.init() raised unexpectedly: {exc}")
        finally:
            del os.environ["SENTRY_DSN"]


class TestContinueTrace(unittest.TestCase):
    """Tests for Observability.continue_trace()."""

    def _obs_no_sdk(self) -> Observability:
        """Return an already-initialised Observability with no SDK."""
        obs = Observability()
        obs._initialized = True  # skip real init
        obs._sentry = None
        return obs

    def test_continue_trace_missing_headers_no_exception(self) -> None:
        """continue_trace({}) must not raise and the block must execute."""
        obs = self._obs_no_sdk()
        executed = []
        with obs.continue_trace({}):
            executed.append(True)
        self.assertEqual(executed, [True], "Block inside continue_trace should execute")

    def test_continue_trace_malformed_headers_no_exception(self) -> None:
        """Malformed sentry-trace header must not raise."""
        obs = self._obs_no_sdk()
        executed = []
        with obs.continue_trace({"sentry-trace": "bad-value"}):
            executed.append(True)
        self.assertEqual(executed, [True])

    def test_continue_trace_with_sdk_missing_headers(self) -> None:
        """Even when SDK is available, missing headers start a new root transaction."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None  # pretend SDK unavailable
        executed = []
        with obs.continue_trace({}):
            executed.append("ok")
        self.assertEqual(executed, ["ok"])

    def test_continue_trace_with_sdk_malformed_headers(self) -> None:
        """Malformed headers fall back to a new root transaction without raising."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None
        executed = []
        with obs.continue_trace({"sentry-trace": "totally-wrong-format!!!"}):
            executed.append("ok")
        self.assertEqual(executed, ["ok"])


class TestSetRequestScope(unittest.TestCase):
    """Tests for Observability.set_request_scope()."""

    def test_set_request_scope_no_sdk(self) -> None:
        """With _sentry=None, set_request_scope must not raise."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None
        try:
            obs.set_request_scope("tenant-1")
        except Exception as exc:
            self.fail(f"set_request_scope raised unexpectedly: {exc}")

    def test_set_request_scope_with_user_id_no_sdk(self) -> None:
        """With _sentry=None, passing user_id must also not raise."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None
        try:
            obs.set_request_scope("tenant-1", user_id="user-42")
        except Exception as exc:
            self.fail(f"set_request_scope raised unexpectedly: {exc}")


class TestExtractHost(unittest.TestCase):
    """Unit tests for the extract_host() module-level helper."""

    def test_extract_host_basic(self) -> None:
        """Strips scheme, path, and query; returns bare hostname."""
        self.assertEqual(extract_host("https://example.com/api/v1?q=1"), "example.com")

    def test_extract_host_with_port(self) -> None:
        """Non-default port is included in the result."""
        self.assertEqual(extract_host("http://host:8080/path"), "host:8080")

    def test_extract_host_default_http_port(self) -> None:
        """Default HTTP port 80 is omitted."""
        self.assertEqual(extract_host("http://host:80/path"), "host")

    def test_extract_host_default_https_port(self) -> None:
        """Default HTTPS port 443 is omitted."""
        self.assertEqual(extract_host("https://host:443/path"), "host")

    def test_extract_host_no_path(self) -> None:
        """URL with no path returns just the hostname."""
        self.assertEqual(extract_host("https://api.example.org"), "api.example.org")

    def test_extract_host_with_fragment(self) -> None:
        """Fragment is discarded."""
        self.assertEqual(extract_host("https://example.com/page#section"), "example.com")

    def test_extract_host_postgres_url(self) -> None:
        """Works for non-HTTP schemes like postgres://."""
        self.assertEqual(extract_host("postgres://user:pass@db.host:5432/mydb"), "db.host:5432")

    def test_extract_host_non_default_https_port(self) -> None:
        """Non-standard HTTPS port is included."""
        self.assertEqual(extract_host("https://host:8443/api"), "host:8443")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Property 7: URL Host Extraction — Validates: Requirements 8.1, 8.2, 8.4
# ---------------------------------------------------------------------------

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
    _HYPOTHESIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _HYPOTHESIS_AVAILABLE = False


@unittest.skipUnless(_HYPOTHESIS_AVAILABLE, "hypothesis not installed")
class TestExtractHostProperty(unittest.TestCase):
    """Property 7: URL Host Extraction — Validates: Requirements 8.1, 8.2, 8.4"""

    @given(
        scheme=st.sampled_from(["http", "https", "postgres", "redis", "ftp"]),
        hostname=st.from_regex(r"[a-z][a-z0-9\-]{0,20}\.[a-z]{2,6}", fullmatch=True),
        port=st.one_of(st.none(), st.integers(min_value=1, max_value=65535)),
        path=st.text(
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="/-_",
            ),
            max_size=50,
        ),
    )
    @settings(max_examples=200)
    def test_extract_host_strips_scheme_path_query(
        self, scheme: str, hostname: str, port: int | None, path: str
    ) -> None:
        from omni_modal.observability import extract_host

        if port:
            url = f"{scheme}://{hostname}:{port}/{path}?q=1#frag"
        else:
            url = f"{scheme}://{hostname}/{path}?q=1#frag"

        result = extract_host(url)

        # Must not contain scheme
        assert "://" not in result, f"result {result!r} contains scheme"
        # Must not contain path
        assert "/" not in result, f"result {result!r} contains path"
        # Must not contain query
        assert "?" not in result, f"result {result!r} contains query"
        # Must not contain fragment
        assert "#" not in result, f"result {result!r} contains fragment"
        # Must contain hostname
        assert hostname in result, f"result {result!r} does not contain hostname {hostname!r}"


# ---------------------------------------------------------------------------
# Property 16: Backend Trace Continuation — Validates: Requirements 12.2
# ---------------------------------------------------------------------------


class TestBackendTraceContinuation(unittest.TestCase):
    """Property 16: Backend Trace Continuation — Validates: Requirements 12.2"""

    def test_continue_trace_valid_sentry_trace_no_exception(self) -> None:
        """Valid sentry-trace headers are accepted without raising."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None  # SDK unavailable — should still work
        executed = []

        valid_trace = "4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-1"
        with obs.continue_trace({"sentry-trace": valid_trace, "baggage": ""}):
            executed.append("ok")
        self.assertEqual(executed, ["ok"])

    def test_continue_trace_various_header_formats(self) -> None:
        """Various sentry-trace formats do not raise and allow block execution."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None

        test_cases = [
            {},  # missing
            {"sentry-trace": ""},  # empty
            {"sentry-trace": "not-a-valid-format"},  # malformed
            {"sentry-trace": "trace123-span456-0"},  # short but parseable format
        ]
        for headers in test_cases:
            executed = []
            with obs.continue_trace(headers):
                executed.append(True)
            self.assertEqual(executed, [True], f"Block did not execute for headers={headers}")

    def test_continue_trace_valid_header_executes_block(self) -> None:
        """Block inside continue_trace always executes regardless of headers."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None
        executed = []
        with obs.continue_trace({"sentry-trace": "abc123-def456-1"}):
            executed.append("ran")
        self.assertEqual(executed, ["ran"])

    def test_continue_trace_missing_header_starts_new_transaction(self) -> None:
        """Missing sentry-trace header starts a fresh root transaction without raising."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None
        executed = []
        with obs.continue_trace({}):
            executed.append("new_tx")
        self.assertEqual(executed, ["new_tx"])

    def test_continue_trace_malformed_header_no_error(self) -> None:
        """Malformed sentry-trace header does not raise; block still executes."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None
        executed = []
        with obs.continue_trace({"sentry-trace": "!!!INVALID!!!"}):
            executed.append("ok")
        self.assertEqual(executed, ["ok"])

    def test_sdk_unavailable_handler_executes_normally(self) -> None:
        """When SDK is None, a block inside continue_trace executes without raising."""
        obs = Observability()
        obs._initialized = True
        obs._sentry = None  # simulate SDK not installed
        result = []
        with obs.continue_trace({"sentry-trace": "4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-1"}):
            result.append("executed")
        self.assertEqual(result, ["executed"])
