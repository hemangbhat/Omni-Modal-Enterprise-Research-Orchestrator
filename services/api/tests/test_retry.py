"""Property-based and unit tests for omni_modal.retry.

Tests:
  - Property 2: Exponential Backoff with Jitter Bounds
  - Property 3: Retryable Classification Correctness
  - Property 4: Retry-After Header Override
  - Property 5: Retry Breadcrumb Count Matches Attempt Count
  - Property 6: String Truncation Preserves Prefix
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Ensure the src directory is on the path
# ---------------------------------------------------------------------------
import _path  # noqa: F401

# ---------------------------------------------------------------------------
# hypothesis imports (property-based testing)
# ---------------------------------------------------------------------------
from hypothesis import given, settings
import hypothesis.strategies as st


# ===========================================================================
# Property 2: Exponential Backoff with Jitter Bounds
# Validates: Requirements 9.1, 9.7
# ===========================================================================

class TestBackoffJitterBounds(unittest.TestCase):
    """**Validates: Requirements 9.1, 9.7**"""

    @given(
        base_delay=st.floats(min_value=0.001, max_value=10.0),
        attempt=st.integers(min_value=0, max_value=10),
        jitter_factor=st.floats(min_value=0.0, max_value=1.0),
    )
    @settings(max_examples=200)
    def test_backoff_jitter_bounds(
        self,
        base_delay: float,
        attempt: int,
        jitter_factor: float,
    ) -> None:
        """**Validates: Requirements 9.1, 9.7**

        For any base_delay > 0, attempt >= 0, and jitter_factor in [0, 1],
        compute_delay must satisfy:
          base_delay * 2**attempt  <=  delay  <=  base_delay * 2**attempt * (1 + jitter_factor)
        """
        from omni_modal.retry import compute_delay

        delay = compute_delay(attempt, base_delay, jitter_factor)
        expected_base = base_delay * (2 ** attempt)

        self.assertGreaterEqual(
            delay, expected_base,
            f"delay {delay} should be >= expected_base {expected_base}"
        )
        self.assertLessEqual(
            delay, expected_base * (1 + jitter_factor) + 1e-9,
            f"delay {delay} should be <= expected_base*(1+jitter) {expected_base * (1 + jitter_factor)}"
        )


# ===========================================================================
# Property 3: Retryable Exception Classification
# Validates: Requirements 9.4, 9.5
# ===========================================================================

# --- Helper exception classes for testing ---

class _Http429(Exception):
    status_code = 429

class _Http502(Exception):
    status_code = 502

class _Http503(Exception):
    status_code = 503

class _Http504(Exception):
    status_code = 504

class _Http400(Exception):
    status_code = 400

class _Http401(Exception):
    status_code = 401

class _Http403(Exception):
    status_code = 403

class _Http429ViaStatus(Exception):
    status = 429

class _Http502ViaStatus(Exception):
    status = 502

class _Http400ViaStatus(Exception):
    status = 400

class _Http403ViaStatus(Exception):
    status = 403


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _Http429ViaResponse(Exception):
    def __init__(self) -> None:
        super().__init__()
        self.response = _FakeResponse(429)


class _Http503ViaResponse(Exception):
    def __init__(self) -> None:
        super().__init__()
        self.response = _FakeResponse(503)


class _Http400ViaResponse(Exception):
    def __init__(self) -> None:
        super().__init__()
        self.response = _FakeResponse(400)


class ConnectionTimeoutError(Exception):
    """Class name contains 'ConnectionTimeout'."""


class DBConnectionError(Exception):
    """Class name contains 'DBConnectionError'."""


class ValidationError(Exception):
    """Exact class name 'ValidationError'."""


class FileFormatError(Exception):
    """Exact class name 'FileFormatError'."""


_RETRYABLE_INSTANCES = [
    ConnectionError("connection refused"),
    TimeoutError("timed out"),
    _Http429(),
    _Http502(),
    _Http503(),
    _Http504(),
    _Http429ViaStatus(),
    _Http502ViaStatus(),
    _Http429ViaResponse(),
    _Http503ViaResponse(),
    ConnectionTimeoutError("db conn timeout"),
    DBConnectionError("db gone away"),
]

_NON_RETRYABLE_INSTANCES = [
    _Http400(),
    _Http401(),
    _Http403(),
    _Http400ViaStatus(),
    _Http403ViaStatus(),
    _Http400ViaResponse(),
    ValidationError("bad data"),
    FileFormatError("not a pdf"),
    ValueError("generic"),
    RuntimeError("generic runtime"),
    KeyError("key"),
]


class TestRetryableClassification(unittest.TestCase):
    """**Validates: Requirements 9.4, 9.5**"""

    @given(st.sampled_from(_RETRYABLE_INSTANCES))
    @settings(max_examples=100)
    def test_retryable_exceptions_return_true(self, exc: BaseException) -> None:
        """**Validates: Requirements 9.4, 9.5**

        All exceptions in the retryable list must return True from is_retryable().
        """
        from omni_modal.retry import is_retryable
        self.assertTrue(
            is_retryable(exc),
            f"Expected is_retryable({type(exc).__name__}) == True, got False"
        )

    @given(st.sampled_from(_NON_RETRYABLE_INSTANCES))
    @settings(max_examples=100)
    def test_non_retryable_exceptions_return_false(self, exc: BaseException) -> None:
        """**Validates: Requirements 9.4, 9.5**

        All exceptions in the non-retryable list must return False from is_retryable().
        """
        from omni_modal.retry import is_retryable
        self.assertFalse(
            is_retryable(exc),
            f"Expected is_retryable({type(exc).__name__}) == False, got True"
        )


# ===========================================================================
# Property 4: Retry-After Header Override
# Validates: Requirements 9.8
# ===========================================================================

class _RetryAfterException(Exception):
    """Exception that carries a retry_after attribute (like an HTTP 429)."""
    status_code = 429

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class TestRetryAfterOverride(unittest.TestCase):
    """**Validates: Requirements 9.8**"""

    @given(retry_after_value=st.floats(min_value=0.1, max_value=60.0))
    @settings(max_examples=200)
    def test_retry_after_used_when_within_budget(self, retry_after_value: float) -> None:
        """**Validates: Requirements 9.8**

        When Retry-After value <= max_total_delay, the decorator should sleep
        for the Retry-After value (not the exponential backoff).
        When Retry-After > max_total_delay, the decorator falls back to the
        standard exponential backoff delay (base_delay * 2^attempt).
        """
        from omni_modal.retry import retry_with_backoff, is_retryable

        max_total_delay = 30.0
        base_delay = 1.0
        jitter_factor = 0.0
        # With attempt=0 (first retry), exponential = base_delay * 2^0 = 1.0
        expected_exponential = base_delay * (2 ** 0)

        call_count = 0
        slept_values: list[float] = []

        exc_to_raise = _RetryAfterException(retry_after_value)

        def always_fails() -> None:
            nonlocal call_count
            call_count += 1
            raise exc_to_raise

        with patch("omni_modal.retry.time.sleep") as mock_sleep, \
             patch("omni_modal.retry.observability") as mock_obs:
            mock_sleep.side_effect = lambda d: slept_values.append(d)

            decorated = retry_with_backoff(
                always_fails,
                max_retries=1,
                base_delay=base_delay,
                max_total_delay=max_total_delay,
                jitter_factor=jitter_factor,
                retryable_exceptions=(Exception,),
                retryable=is_retryable,
                respect_retry_after=True,
            )

            try:
                decorated()
            except Exception:
                pass

        if retry_after_value <= max_total_delay:
            # Should have slept using Retry-After value
            self.assertEqual(
                len(slept_values), 1,
                f"Expected exactly 1 sleep call when retry_after={retry_after_value} <= {max_total_delay}"
            )
            self.assertAlmostEqual(
                slept_values[0], retry_after_value, places=9,
                msg=f"Sleep value {slept_values[0]} should equal retry_after={retry_after_value}"
            )
        else:
            # Retry-After exceeds budget → fall back to exponential backoff
            # With max_retries=1, we get 1 retry (1 sleep) using exponential
            self.assertEqual(
                len(slept_values), 1,
                f"Expected 1 sleep (exponential fallback) when retry_after={retry_after_value} > {max_total_delay}"
            )
            self.assertAlmostEqual(
                slept_values[0], expected_exponential, places=9,
                msg=f"Sleep value {slept_values[0]} should equal exponential {expected_exponential} "
                    f"when retry_after={retry_after_value} > {max_total_delay}"
            )


# ===========================================================================
# Property 5: Retry Breadcrumb Count Matches Attempt Count
# Validates: Requirements 9.2, 9.3
# ===========================================================================

class TestRetryBreadcrumbCount(unittest.TestCase):
    """**Validates: Requirements 9.2, 9.3**"""

    @given(max_retries=st.integers(min_value=1, max_value=5))
    @settings(max_examples=50)
    def test_breadcrumb_count_equals_max_retries_on_total_failure(
        self, max_retries: int
    ) -> None:
        """**Validates: Requirements 9.2, 9.3**

        When the function fails on every call, the number of retry breadcrumbs
        recorded SHALL equal max_retries, and the final capture SHALL include
        total_attempts = max_retries.
        """
        from omni_modal.retry import retry_with_backoff

        def always_fails() -> None:
            raise ConnectionError("simulated failure")

        with patch("omni_modal.retry.time.sleep"), \
             patch("omni_modal.retry.observability") as mock_obs:
            mock_obs.add_breadcrumb = MagicMock()
            mock_obs.capture_exception = MagicMock()

            decorated = retry_with_backoff(
                always_fails,
                max_retries=max_retries,
                base_delay=0.001,
                max_total_delay=9999.0,  # don't let budget stop us
                jitter_factor=0.0,
                retryable_exceptions=(Exception,),
                retryable=lambda _exc: True,
                respect_retry_after=False,
            )

            try:
                decorated()
            except Exception:
                pass

        # Count only retry attempt breadcrumbs (not recovery breadcrumbs)
        retry_breadcrumb_calls = [
            c for c in mock_obs.add_breadcrumb.call_args_list
            if c.kwargs.get("category") == "retry"
               and "Retry attempt" in (c.kwargs.get("message") or "")
        ]

        self.assertEqual(
            len(retry_breadcrumb_calls), max_retries,
            f"Expected {max_retries} retry breadcrumbs, got {len(retry_breadcrumb_calls)}"
        )

        # Check final capture includes total_attempts = max_retries
        self.assertTrue(
            mock_obs.capture_exception.called,
            "capture_exception should have been called on exhaustion"
        )
        capture_kwargs = mock_obs.capture_exception.call_args.kwargs
        context = capture_kwargs.get("context", {})
        self.assertEqual(
            context.get("total_attempts"), max_retries,
            f"capture context total_attempts should be {max_retries}, "
            f"got {context.get('total_attempts')}"
        )


# ===========================================================================
# Property 6: String Truncation Preserves Prefix
# Validates: Requirements 6.3, 6.4, 8.3
# ===========================================================================

class TestStringTruncation(unittest.TestCase):
    """**Validates: Requirements 6.3, 6.4, 8.3**"""

    @given(
        s=st.text(min_size=0, max_size=2000),
        limit=st.sampled_from([256, 512, 500]),
    )
    @settings(max_examples=300)
    def test_truncate_preserves_prefix(self, s: str, limit: int) -> None:
        """**Validates: Requirements 6.3, 6.4, 8.3**

        For any string s and limit K:
          - If len(s) <= K: truncate(s, K) == s
          - If len(s) > K:  truncate(s, K) == s[:K]
        """
        from omni_modal.retry import truncate

        result = truncate(s, limit)

        if len(s) <= limit:
            self.assertEqual(
                result, s,
                f"truncate should return the original string when len={len(s)} <= limit={limit}"
            )
        else:
            self.assertEqual(
                result, s[:limit],
                f"truncate should return first {limit} chars when len={len(s)} > limit"
            )
            self.assertEqual(len(result), limit)

    @given(
        s=st.text(min_size=0, max_size=2000),
        limit=st.integers(min_value=0, max_value=2000),
    )
    @settings(max_examples=300)
    def test_truncate_arbitrary_limits(self, s: str, limit: int) -> None:
        """**Validates: Requirements 6.3, 6.4, 8.3**

        Truncation holds for arbitrary integer limits in [0, 2000].
        """
        from omni_modal.retry import truncate

        result = truncate(s, limit)

        if len(s) <= limit:
            self.assertEqual(result, s)
        else:
            self.assertEqual(result, s[:limit])
            self.assertEqual(len(result), limit)


# ===========================================================================
# Unit tests for additional coverage
# ===========================================================================

class TestIsRetryableUnit(unittest.TestCase):
    """Unit tests for is_retryable edge cases."""

    def test_connection_error(self) -> None:
        from omni_modal.retry import is_retryable
        self.assertTrue(is_retryable(ConnectionError()))

    def test_timeout_error(self) -> None:
        from omni_modal.retry import is_retryable
        self.assertTrue(is_retryable(TimeoutError()))

    def test_validation_error_not_retryable(self) -> None:
        from omni_modal.retry import is_retryable
        self.assertFalse(is_retryable(ValidationError()))

    def test_file_format_error_not_retryable(self) -> None:
        from omni_modal.retry import is_retryable
        self.assertFalse(is_retryable(FileFormatError()))

    def test_generic_exception_not_retryable(self) -> None:
        from omni_modal.retry import is_retryable
        self.assertFalse(is_retryable(ValueError("bad")))

    def test_http_500_not_retryable(self) -> None:
        from omni_modal.retry import is_retryable

        class Http500(Exception):
            status_code = 500

        self.assertFalse(is_retryable(Http500()))

    def test_response_status_code_503_retryable(self) -> None:
        from omni_modal.retry import is_retryable
        exc = _Http503ViaResponse = Exception("svc unavailable")
        exc.response = _FakeResponse(503)  # type: ignore[attr-defined]
        self.assertTrue(is_retryable(exc))

    def test_dbconnectionerror_by_name(self) -> None:
        from omni_modal.retry import is_retryable
        self.assertTrue(is_retryable(DBConnectionError("gone")))

    def test_connection_timeout_by_name(self) -> None:
        from omni_modal.retry import is_retryable
        self.assertTrue(is_retryable(ConnectionTimeoutError("timeout")))


class TestComputeDelayUnit(unittest.TestCase):
    """Unit tests for compute_delay."""

    def test_zero_jitter_equals_exponential(self) -> None:
        from omni_modal.retry import compute_delay
        # With jitter_factor=0.0, delay should equal base_delay * 2**attempt exactly
        for attempt in range(5):
            self.assertAlmostEqual(
                compute_delay(attempt, 1.0, 0.0),
                1.0 * (2 ** attempt),
                places=10,
            )

    def test_delay_increases_with_attempt(self) -> None:
        from omni_modal.retry import compute_delay
        delays = [compute_delay(i, 1.0, 0.0) for i in range(6)]
        for i in range(1, len(delays)):
            self.assertGreater(delays[i], delays[i - 1])


class TestTruncateUnit(unittest.TestCase):
    """Unit tests for truncate."""

    def test_short_string_unchanged(self) -> None:
        from omni_modal.retry import truncate
        self.assertEqual(truncate("hello", 10), "hello")

    def test_exact_length_unchanged(self) -> None:
        from omni_modal.retry import truncate
        self.assertEqual(truncate("hello", 5), "hello")

    def test_long_string_truncated(self) -> None:
        from omni_modal.retry import truncate
        self.assertEqual(truncate("hello world", 5), "hello")

    def test_zero_limit(self) -> None:
        from omni_modal.retry import truncate
        self.assertEqual(truncate("anything", 0), "")

    def test_empty_string(self) -> None:
        from omni_modal.retry import truncate
        self.assertEqual(truncate("", 256), "")


class TestRetryWithBackoffUnit(unittest.TestCase):
    """Unit tests for retry_with_backoff decorator behavior."""

    def test_succeeds_without_retry(self) -> None:
        from omni_modal.retry import retry_with_backoff

        with patch("omni_modal.retry.time.sleep"), \
             patch("omni_modal.retry.observability"):
            @retry_with_backoff(max_retries=3, base_delay=0.001)
            def succeed() -> str:
                return "ok"

            self.assertEqual(succeed(), "ok")

    def test_retries_then_succeeds(self) -> None:
        from omni_modal.retry import retry_with_backoff

        attempts = 0

        with patch("omni_modal.retry.time.sleep"), \
             patch("omni_modal.retry.observability") as mock_obs:
            mock_obs.add_breadcrumb = MagicMock()
            mock_obs.capture_exception = MagicMock()

            @retry_with_backoff(
                max_retries=3,
                base_delay=0.001,
                jitter_factor=0.0,
                retryable_exceptions=(ConnectionError,),
                retryable=lambda _: True,
            )
            def flaky() -> str:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise ConnectionError("transient")
                return "success"

            result = flaky()

        self.assertEqual(result, "success")
        self.assertEqual(attempts, 3)
        # Should have recovery breadcrumb
        recovery_calls = [
            c for c in mock_obs.add_breadcrumb.call_args_list
            if "Retry succeeded" in (c.kwargs.get("message") or "")
        ]
        self.assertEqual(len(recovery_calls), 1)

    def test_max_total_delay_stops_early(self) -> None:
        from omni_modal.retry import retry_with_backoff

        slept: list[float] = []

        with patch("omni_modal.retry.time.sleep", side_effect=lambda d: slept.append(d)), \
             patch("omni_modal.retry.observability"):
            @retry_with_backoff(
                max_retries=10,
                base_delay=1.0,
                max_total_delay=5.0,
                jitter_factor=0.0,
                retryable_exceptions=(Exception,),
                retryable=lambda _: True,
            )
            def always_fails() -> None:
                raise RuntimeError("fail")

            with self.assertRaises(RuntimeError):
                always_fails()

        # With base=1.0 and no jitter: delays are 1, 2, 4, 8...
        # After sleeping 1+2=3, next would be 4 → 3+4=7 > 5 → stop
        total_slept = sum(slept)
        self.assertLessEqual(total_slept, 5.0)

    def test_non_retryable_raises_immediately(self) -> None:
        from omni_modal.retry import retry_with_backoff

        call_count = 0

        with patch("omni_modal.retry.time.sleep"), \
             patch("omni_modal.retry.observability"):
            @retry_with_backoff(
                max_retries=5,
                base_delay=0.001,
                retryable_exceptions=(Exception,),
                retryable=lambda _: False,
            )
            def raises_non_retryable() -> None:
                nonlocal call_count
                call_count += 1
                raise ValueError("not retryable")

            with self.assertRaises(ValueError):
                raises_non_retryable()

        # Should only be called once (no retries)
        self.assertEqual(call_count, 1)

    def test_breadcrumb_data_includes_required_fields(self) -> None:
        from omni_modal.retry import retry_with_backoff

        with patch("omni_modal.retry.time.sleep"), \
             patch("omni_modal.retry.observability") as mock_obs:
            mock_obs.add_breadcrumb = MagicMock()
            mock_obs.capture_exception = MagicMock()

            @retry_with_backoff(
                max_retries=1,
                base_delay=0.001,
                jitter_factor=0.0,
                retryable_exceptions=(Exception,),
                retryable=lambda _: True,
            )
            def fails_once() -> None:
                raise ConnectionError("test")

            try:
                fails_once()
            except ConnectionError:
                pass

        # Find breadcrumb with category="retry"
        retry_calls = [
            c for c in mock_obs.add_breadcrumb.call_args_list
            if c.kwargs.get("category") == "retry"
               and "Retry attempt" in (c.kwargs.get("message") or "")
        ]
        self.assertGreater(len(retry_calls), 0)
        data = retry_calls[0].kwargs.get("data", {})
        self.assertIn("attempt_number", data)
        self.assertIn("delay_seconds", data)
        self.assertIn("exception_type", data)
        self.assertEqual(data["exception_type"], "ConnectionError")


if __name__ == "__main__":
    unittest.main()
