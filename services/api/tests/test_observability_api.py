"""Tests for Phase 4 observability: correlation IDs, /metrics, /health/ready."""

from __future__ import annotations

import os
import unittest

import _path  # noqa: F401

os.environ.setdefault("JWT_SECRET", "test-secret-for-obs")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("REDIS_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.metrics import Metrics  # noqa: E402


class ObservabilityApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)

    def test_correlation_id_generated_and_echoed(self) -> None:
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn("X-Correlation-ID", r.headers)
        self.assertTrue(r.headers["X-Correlation-ID"])

    def test_inbound_correlation_id_is_honoured(self) -> None:
        r = self.client.get("/health", headers={"X-Correlation-ID": "trace-abc-123"})
        self.assertEqual(r.headers.get("X-Correlation-ID"), "trace-abc-123")

    def test_readiness_ok_without_configured_deps(self) -> None:
        r = self.client.get("/health/ready")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "ready")
        self.assertIn("database", body["checks"])
        self.assertIn("redis", body["checks"])

    def test_metrics_exposition_after_requests(self) -> None:
        self.client.get("/health")
        r = self.client.get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("http_requests_total", r.text)
        self.assertIn("http_request_duration_seconds", r.text)


class MetricsRegistryTests(unittest.TestCase):
    def test_counter_accumulates(self) -> None:
        m = Metrics()
        m.inc_counter("hits", {"route": "/x"})
        m.inc_counter("hits", {"route": "/x"})
        m.inc_counter("hits", {"route": "/y"})
        out = m.render()
        self.assertIn('hits{route="/x"} 2', out)
        self.assertIn('hits{route="/y"} 1', out)

    def test_histogram_buckets_and_count(self) -> None:
        m = Metrics(buckets=(0.1, 1.0))
        m.observe("lat", 0.05)   # <= 0.1
        m.observe("lat", 0.5)    # <= 1.0
        m.observe("lat", 5.0)    # +Inf only
        out = m.render()
        self.assertIn("lat_count 3", out)
        self.assertIn('lat_bucket{le="0.1"} 1', out)
        self.assertIn('lat_bucket{le="1"} 2', out)
        self.assertIn('lat_bucket{le="+Inf"} 3', out)

    def test_render_is_valid_prometheus_type_lines(self) -> None:
        m = Metrics()
        m.inc_counter("c")
        self.assertIn("# TYPE c counter", m.render())


if __name__ == "__main__":
    unittest.main()
