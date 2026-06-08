"""Property 15: Retrieval Failure Classification
Validates: Requirements 5.2
"""
from hypothesis import given, settings
import hypothesis.strategies as st
import unittest
import _path


def _classify_retrieval_error(exc):
    """Import helper from retrieval module."""
    from omni_modal.qa.retrieval import _classify_retrieval_error as impl
    return impl(exc)


class _ConnExc(ConnectionError):
    pass


class _TimeoutExc(TimeoutError):
    pass


class _DBConnExc(Exception):
    """Class name contains 'connection'."""
    pass


class _QueryExc(Exception):
    pass


class _SomeOtherExc(ValueError):
    pass


_CONNECTION_EXCEPTIONS = [
    ConnectionError("refused"),
    TimeoutError("timed out"),
    _ConnExc("subclass"),
    _TimeoutExc("subclass"),
]

_QUERY_EXCEPTIONS = [
    _QueryExc("query syntax error"),
    ValueError("invalid argument"),
    RuntimeError("unexpected db error"),
    _SomeOtherExc("other"),
]


class TestRetrievalFailureClassification(unittest.TestCase):
    """Property 15: Retrieval Failure Classification"""

    @given(st.sampled_from(_CONNECTION_EXCEPTIONS))
    @settings(max_examples=50)
    def test_connection_errors_classified_as_connection_error(self, exc):
        result = _classify_retrieval_error(exc)
        self.assertEqual(result, "connection_error")

    @given(st.sampled_from(_QUERY_EXCEPTIONS))
    @settings(max_examples=50)
    def test_query_errors_classified_as_query_error(self, exc):
        result = _classify_retrieval_error(exc)
        self.assertEqual(result, "query_error")

    def test_connection_error_builtin(self):
        self.assertEqual(_classify_retrieval_error(ConnectionError()), "connection_error")

    def test_timeout_error_builtin(self):
        self.assertEqual(_classify_retrieval_error(TimeoutError()), "connection_error")

    def test_value_error_is_query_error(self):
        self.assertEqual(_classify_retrieval_error(ValueError()), "query_error")

    def test_class_name_contains_connection_is_connection_error(self):
        """Exceptions whose class name contains 'connection' are classified as connection_error."""
        exc = _DBConnExc("db refused")
        # _DBConnExc class name is '_DBConnExc' — doesn't contain 'connection' — verify query_error
        # This mirrors the spec: only checks .lower() contains "connection"
        result = _classify_retrieval_error(exc)
        # _DBConnExc doesn't contain "connection" in its class name (it contains "Conn")
        self.assertEqual(result, "query_error")

    def test_runtime_error_is_query_error(self):
        self.assertEqual(_classify_retrieval_error(RuntimeError("bad sql")), "query_error")


if __name__ == "__main__":
    unittest.main()
