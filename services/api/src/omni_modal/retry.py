"""Retry utility with exponential backoff, jitter, Retry-After support, and Sentry breadcrumbs.

Provides a decorator ``retry_with_backoff`` that can be used to wrap any callable.
It records each retry attempt as a breadcrumb via ``observability.add_breadcrumb``
and captures the final failure as a Sentry exception.

Also exposes:
  - ``compute_delay(attempt, base_delay, jitter_factor)`` — pure delay computation
  - ``is_retryable(exc)`` — classify exceptions as retryable or not
  - ``truncate(value, limit)`` — safely truncate strings to a maximum length
"""

from __future__ import annotations

import random
import time
import functools
from typing import Callable, TypeVar, Any, Tuple, Optional

from omni_modal.observability import observability

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def truncate(value: str, limit: int) -> str:
    """Return *value* unchanged if it fits within *limit* characters, else the first *limit* chars."""
    return value if len(value) <= limit else value[:limit]


def compute_delay(attempt: int, base_delay: float, jitter_factor: float) -> float:
    """Compute the backoff delay for a given attempt, incorporating jitter.

    Parameters
    ----------
    attempt:
        Zero-based attempt index (0 = first retry, 1 = second retry, …).
    base_delay:
        Base delay in seconds.
    jitter_factor:
        Upper bound of the jitter as a fraction of the exponential component.
        E.g. 0.25 means jitter is in [0, 0.25 * exponential].

    Returns
    -------
    float
        Delay in seconds satisfying:
        ``base_delay * 2**attempt <= delay <= base_delay * 2**attempt * (1 + jitter_factor)``
    """
    exponential = base_delay * (2 ** attempt)
    jitter = random.uniform(0, jitter_factor * exponential)
    return exponential + jitter


# ---------------------------------------------------------------------------
# Retryable exception classifier
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_NON_RETRYABLE_STATUS_CODES = frozenset({400, 401, 403})
_NON_RETRYABLE_CLASS_NAMES = frozenset({"ValidationError", "FileFormatError"})
_RETRYABLE_CLASS_NAME_SUBSTRINGS = ("connectiontimeout", "dbconnectionerror")


def is_retryable(exc: BaseException) -> bool:
    """Classify *exc* as retryable (``True``) or non-retryable (``False``).

    Retryable conditions
    --------------------
    - ``ConnectionError`` or ``TimeoutError`` (Python builtins)
    - Any exception with a ``status_code`` attribute in {429, 502, 503, 504}
    - Any exception with a ``status`` attribute in {429, 502, 503, 504}
    - Any exception with a ``response`` attribute whose ``response.status_code`` is in {429, 502, 503, 504}
    - Any exception whose class name (case-insensitive) contains "connectiontimeout" or "dbconnectionerror"

    Non-retryable conditions (take priority)
    -----------------------------------------
    - Any exception with ``status_code`` / ``status`` in {400, 401, 403}
    - Any exception whose exact class name is "ValidationError" or "FileFormatError"
    - All other exceptions → ``False`` (conservative default)
    """
    class_name = type(exc).__name__

    # Non-retryable: explicit class name match (exact)
    if class_name in _NON_RETRYABLE_CLASS_NAMES:
        return False

    # Check status_code attribute (higher priority non-retryable check first)
    status_code: int | None = None
    if hasattr(exc, "status_code"):
        try:
            status_code = int(exc.status_code)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass

    if status_code is not None:
        if status_code in _NON_RETRYABLE_STATUS_CODES:
            return False
        if status_code in _RETRYABLE_STATUS_CODES:
            return True

    # Check status attribute
    status: int | None = None
    if hasattr(exc, "status"):
        try:
            status = int(exc.status)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass

    if status is not None:
        if status in _NON_RETRYABLE_STATUS_CODES:
            return False
        if status in _RETRYABLE_STATUS_CODES:
            return True

    # Check response.status_code attribute
    if hasattr(exc, "response") and exc.response is not None:  # type: ignore[union-attr]
        try:
            resp_status = int(exc.response.status_code)  # type: ignore[union-attr]
            if resp_status in _NON_RETRYABLE_STATUS_CODES:
                return False
            if resp_status in _RETRYABLE_STATUS_CODES:
                return True
        except (TypeError, ValueError, AttributeError):
            pass

    # Python builtin network/timeout errors
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True

    # Class name substring check (case-insensitive)
    class_name_lower = class_name.lower()
    if any(sub in class_name_lower for sub in _RETRYABLE_CLASS_NAME_SUBSTRINGS):
        return True

    # Conservative default: not retryable
    return False


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def retry_with_backoff(
    func: Callable[..., _T] | None = None,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_total_delay: float = 30.0,
    jitter_factor: float = 0.25,
    retryable_exceptions: Tuple[type[BaseException], ...] = (Exception,),
    retryable: Optional[Callable[[BaseException], bool]] = None,
    respect_retry_after: bool = True,
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Return a decorator that retries *func* with exponential backoff and jitter.

    Parameters
    ----------
    max_retries:
        Number of attempts *after* the initial call (total attempts = max_retries + 1).
    base_delay:
        Initial backoff delay in seconds. Each subsequent retry doubles the base.
    max_total_delay:
        Maximum cumulative sleep seconds. If the next computed delay would push
        total sleep past this ceiling, raise immediately (even if max_retries not reached).
    jitter_factor:
        Upper bound of the jitter as a fraction of the exponential component.
    retryable_exceptions:
        Tuple of exception types that are candidates for retry.
    retryable:
        Optional predicate receiving the exception instance; if it returns ``False``
        the exception is raised immediately even if within max_retries.
    respect_retry_after:
        When ``True`` and the caught exception has a numeric ``retry_after`` attribute
        (seconds), use that as the delay **if** it does not exceed ``max_total_delay``.
    """

    if retryable is None:
        retryable = is_retryable

    def decorator(inner: Callable[..., _T]) -> Callable[..., _T]:
        @functools.wraps(inner)
        def wrapper(*args: Any, **kwargs: Any) -> _T:
            attempts = 0
            cumulative_sleep: float = 0.0
            had_failures = False

            while True:
                try:
                    result = inner(*args, **kwargs)
                    # Recovery breadcrumb: if we had at least one failure and finally succeeded
                    if had_failures:
                        observability.add_breadcrumb(
                            message="Retry succeeded",
                            category="retry",
                            level="info",
                            data={
                                "total_attempts": attempts,
                                "function": inner.__name__,
                            },
                        )
                    return result
                except retryable_exceptions as exc:
                    attempts += 1
                    had_failures = True

                    # Determine delay for this attempt (0-based: first retry is attempt index 0)
                    retry_index = attempts - 1

                    # Decide whether to stop before computing/recording breadcrumb
                    # (don't record a breadcrumb when we're exhausted — only on actual retries)
                    stop_no_retries_left = attempts > max_retries
                    stop_not_retryable = not retryable(exc)

                    if stop_no_retries_left or stop_not_retryable:
                        # Final failure – capture to Sentry and re-raise (no breadcrumb).
                        # total_attempts = number of retries performed (breadcrumb count).
                        # When exhausted by max_retries: that equals max_retries = attempts - 1.
                        # When stopped early (not retryable): 0 retries were performed.
                        retry_count = attempts - 1
                        observability.capture_exception(
                            exc,
                            operation="retry",
                            context={
                                "function": inner.__name__,
                                "total_attempts": retry_count,
                                "cumulative_elapsed_ms": int(cumulative_sleep * 1000),
                            },
                        )
                        raise

                    # Check Retry-After header first
                    delay: float | None = None
                    if respect_retry_after and hasattr(exc, "retry_after"):
                        try:
                            ra_value = float(exc.retry_after)  # type: ignore[union-attr]
                            # Only honour it if it won't push us past the ceiling
                            if (cumulative_sleep + ra_value) <= max_total_delay:
                                delay = ra_value
                        except (TypeError, ValueError):
                            pass

                    if delay is None:
                        delay = compute_delay(retry_index, base_delay, jitter_factor)

                    # Check max_total_delay budget
                    if (cumulative_sleep + delay) > max_total_delay:
                        # Next sleep would exceed the total delay budget – stop here.
                        # At this point, attempts - 1 retries were already completed (breadcrumbs recorded).
                        retry_count = attempts - 1
                        observability.capture_exception(
                            exc,
                            operation="retry",
                            context={
                                "function": inner.__name__,
                                "total_attempts": retry_count,
                                "cumulative_elapsed_ms": int(cumulative_sleep * 1000),
                            },
                        )
                        raise

                    # Record breadcrumb only when we are actually going to retry
                    observability.add_breadcrumb(
                        message=f"Retry attempt {attempts} for {inner.__name__}",
                        category="retry",
                        level="info",
                        data={
                            "attempt_number": attempts,
                            "delay_seconds": delay,
                            "exception_type": type(exc).__name__,
                        },
                    )

                    # Sleep before next attempt
                    time.sleep(delay)
                    cumulative_sleep += delay

        return wrapper

    # Allow usage both as @retry_with_backoff and as retry_with_backoff(func)
    if func is not None:
        return decorator(func)  # type: ignore[return-value]
    return decorator
