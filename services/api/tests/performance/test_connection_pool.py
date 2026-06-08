"""Tests for db/pool.py — Connection Pool singleton.

Properties:
  - Property 3: Connection pool singleton identity (Validates: Requirements 3.5)
  - Property 4: Pool constructed with env-configured sizes (Validates: Requirements 3.2)

Feature: performance-and-scalability
"""
from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import MagicMock, patch

import _path  # noqa: F401
from hypothesis import given, settings
import hypothesis.strategies as st


class TestConnectionPoolSingleton(unittest.TestCase):
    """Property 3: Connection pool singleton identity — Validates: Requirements 3.5"""

    def setUp(self):
        # Reset singleton before each test
        import omni_modal.db.pool as pool_module
        pool_module.reset_pool_for_testing()

    def tearDown(self):
        import omni_modal.db.pool as pool_module
        pool_module.reset_pool_for_testing()

    @given(call_count=st.integers(min_value=1, max_value=50))
    @settings(max_examples=100)
    def test_get_connection_pool_singleton_identity(self, call_count: int) -> None:
        """Property 3: All calls to get_connection_pool() return the same object identity."""
        import omni_modal.db.pool as pool_module
        pool_module.reset_pool_for_testing()

        mock_pool = MagicMock()
        mock_pool_cls = MagicMock(return_value=mock_pool)

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/test"}), \
             patch("omni_modal.db.pool.ConnectionPool", mock_pool_cls), \
             patch("omni_modal.db.pool._POOL_AVAILABLE", True):

            results = [pool_module.get_connection_pool() for _ in range(call_count)]

        # All returned objects must be the exact same instance
        first = results[0]
        for result in results:
            self.assertIs(result, first, "All calls must return the same pool instance")

        # ConnectionPool constructor called exactly once
        self.assertEqual(mock_pool_cls.call_count, 1)


class TestConnectionPoolEnvConfiguredSizes(unittest.TestCase):
    """Property 4: Pool constructed with env-configured sizes — Validates: Requirements 3.2"""

    def setUp(self):
        import omni_modal.db.pool as pool_module
        pool_module.reset_pool_for_testing()

    def tearDown(self):
        import omni_modal.db.pool as pool_module
        pool_module.reset_pool_for_testing()

    @given(
        min_size=st.integers(min_value=1, max_value=5),
        max_size=st.integers(min_value=6, max_value=20),
    )
    @settings(max_examples=100)
    def test_pool_uses_env_configured_sizes(self, min_size: int, max_size: int) -> None:
        """Property 4: get_connection_pool() uses DB_POOL_MIN and DB_POOL_MAX from env."""
        import omni_modal.db.pool as pool_module
        pool_module.reset_pool_for_testing()

        mock_pool = MagicMock()
        mock_pool_cls = MagicMock(return_value=mock_pool)

        env = {
            "DATABASE_URL": "postgresql://localhost/test",
            "DB_POOL_MIN": str(min_size),
            "DB_POOL_MAX": str(max_size),
        }

        with patch.dict(os.environ, env, clear=False), \
             patch("omni_modal.db.pool.ConnectionPool", mock_pool_cls), \
             patch("omni_modal.db.pool._POOL_AVAILABLE", True):
            pool_module.get_connection_pool()

        call_kwargs = mock_pool_cls.call_args.kwargs
        self.assertEqual(call_kwargs.get("min_size"), min_size)
        self.assertEqual(call_kwargs.get("max_size"), max_size)


class TestConnectionPoolUnit(unittest.TestCase):
    """Unit tests for pool.py."""

    def setUp(self):
        import omni_modal.db.pool as pool_module
        pool_module.reset_pool_for_testing()

    def tearDown(self):
        import omni_modal.db.pool as pool_module
        pool_module.reset_pool_for_testing()

    def test_get_pool_raises_without_database_url(self) -> None:
        """RuntimeError raised when DATABASE_URL is not set."""
        import omni_modal.db.pool as pool_module
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError, msg="DATABASE_URL"):
                pool_module.get_connection_pool()

    def test_close_pool_resets_singleton(self) -> None:
        """After close_connection_pool(), the singleton is None."""
        import omni_modal.db.pool as pool_module
        mock_pool = MagicMock()
        mock_pool_cls = MagicMock(return_value=mock_pool)

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/test"}), \
             patch("omni_modal.db.pool.ConnectionPool", mock_pool_cls), \
             patch("omni_modal.db.pool._POOL_AVAILABLE", True):
            pool_module.get_connection_pool()

        pool_module.close_connection_pool()
        self.assertIsNone(pool_module._pool)

    def test_default_pool_sizes(self) -> None:
        """Without env vars, default min=2 max=10 are used."""
        import omni_modal.db.pool as pool_module
        mock_pool_cls = MagicMock(return_value=MagicMock())

        env = {"DATABASE_URL": "postgresql://localhost/test"}
        # Remove any existing DB_POOL_MIN/MAX from env
        clean_env = {k: v for k, v in os.environ.items() if k not in ("DB_POOL_MIN", "DB_POOL_MAX")}
        clean_env.update(env)

        with patch.dict(os.environ, clean_env, clear=True), \
             patch("omni_modal.db.pool.ConnectionPool", mock_pool_cls), \
             patch("omni_modal.db.pool._POOL_AVAILABLE", True):
            pool_module.get_connection_pool()

        kw = mock_pool_cls.call_args.kwargs
        self.assertEqual(kw.get("min_size"), 2)
        self.assertEqual(kw.get("max_size"), 10)
        self.assertEqual(kw.get("timeout"), 5.0)
