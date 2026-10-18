"""The Postgres-only SQL from M3 and M4 returns the right values (Postgres only).

Counts on the dashboard and project list, the keyword half of hybrid
retrieval, the empty item edit, and the Stripe event log.
"""

from __future__ import annotations

import base64
import os
import tempfile
import time
import unittest
import uuid

import _path  # noqa: F401
from _pdf import make_pdf
from _pg import disposable_pg_url, migrate

URL = disposable_pg_url()


@unittest.skipUnless(URL, "set OMERO_TEST_DATABASE_URL to a disposable Postgres")
class PostgresProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        migrate(URL)
        cls._saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "EMBEDDING_DIMENSIONS", "LOCAL_STORAGE_DIR")}
        cls._dir = tempfile.TemporaryDirectory()
        os.environ.update(DATABASE_URL=URL, EMBEDDING_DIMENSIONS="384", LOCAL_STORAGE_DIR=cls._dir.name)
        os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from omni_modal.api.app import create_app  # noqa: PLC0415
        from omni_modal.api.app import state  # noqa: PLC0415

        cls.state = state
        cls.client = TestClient(create_app())
        cls.client.__enter__()
        state.rate_limiter = None
        reg = cls.client.post("/auth/register", json={
            "email": f"pg-product-{uuid.uuid4().hex[:8]}@example.com", "password": "password123"}).json()
        cls.auth = {"Authorization": f"Bearer {reg['token']}"}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        cls._dir.cleanup()
        for key, value in cls._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _upload(self, text: str, name: str) -> str:
        r = self.client.post("/ingest/upload", headers=self.auth, json={
            "filename": name, "title": name, "content_base64": base64.b64encode(make_pdf(text)).decode()})
        self.assertEqual(r.status_code, 202, r.text)
        self.state.dispatcher.wait_idle()
        return r.json()["document_id"]

    def test_counts_keyword_retrieval_and_empty_edit(self) -> None:
        a = self._upload("General notes about the quarterly offsite and team lunches.", "notes.pdf")
        b = self._upload("Purchase order ZX4471 covers forty replacement scanners.", "order.pdf")
        archived = self._upload("An old memo nobody needs.", "memo.pdf")
        self.client.post(f"/documents/{archived}/archive", headers=self.auth)

        dash = self.client.get("/dashboard", headers=self.auth).json()
        self.assertEqual(dash["documents"]["ready"], 2)
        self.assertEqual(dash["documents"]["archived"], 1)

        pid = self.client.post("/projects", headers=self.auth, json={"name": "Counts"}).json()["id"]
        for doc in (a, b):
            self.client.post(f"/projects/{pid}/documents", headers=self.auth, json={"document_id": doc})
        item = self.client.post(f"/projects/{pid}/items", headers=self.auth,
                                json={"kind": "note", "title": "n1"}).json()["id"]
        self.client.post(f"/projects/{pid}/items", headers=self.auth, json={
            "kind": "finding", "title": "f1", "body": "Scanners [1]",
            "citations": [{"document_id": b, "chunk_id": "c", "title": "order.pdf"}]})
        self.client.post(f"/projects/{pid}/reports", headers=self.auth)
        listed = next(p for p in self.client.get("/projects", headers=self.auth).json()["projects"]
                      if p["id"] == pid)
        self.assertEqual(listed["counts"], {"documents": 2, "findings": 1, "notes": 1, "reports": 1})

        empty = self.client.patch(f"/projects/{pid}/items/{item}", headers=self.auth, json={})
        self.assertEqual(empty.status_code, 200, empty.text)

        answer = self.client.post("/query", headers=self.auth, json={"question": "What is ZX4471 for?", "top_k": 1})
        self.assertEqual(answer.json()["citations"][0]["document_id"], b)

    def test_stripe_event_log(self) -> None:
        from omni_modal.saas.billing_events import AppliedEvent, PostgresBillingEventStore  # noqa: PLC0415
        from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

        store = PostgresBillingEventStore(get_connection_pool())
        tenant, eid = f"t-{uuid.uuid4().hex[:6]}", f"evt_{uuid.uuid4().hex}"
        self.assertFalse(store.seen(eid))
        now = time.time()
        self.assertTrue(store.record(AppliedEvent(eid, "customer.subscription.deleted", tenant, now)))
        self.assertFalse(store.record(AppliedEvent(eid, "customer.subscription.deleted", tenant, now)))
        self.assertTrue(store.seen(eid))
        self.assertAlmostEqual(store.last_plan_event_at(tenant), now, places=3)
        self.assertIsNone(store.last_plan_event_at("nobody"))


if __name__ == "__main__":
    unittest.main()
