"""Integration tests for the Postgres-backed SaaS stores (Phase B).

These run only when DATABASE_URL is configured (local dev with Neon); they are
skipped in CI/offline environments where the in-memory stores are used instead.
Each test cleans up the rows it creates.
"""

from __future__ import annotations

import os
import unittest
import uuid

import _path  # noqa: F401

DATABASE_URL = os.environ.get("DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "DATABASE_URL not set — Postgres tests skipped (in-memory path used).")
class PostgresSaasStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from omni_modal.saas.repository import select_usage_store, select_workspace_store

        cls.ws = select_workspace_store()
        cls.us = select_usage_store()
        from omni_modal.saas.repository import PostgresWorkspaceStore

        if not isinstance(cls.ws, PostgresWorkspaceStore):
            raise unittest.SkipTest("Pool unavailable; Postgres store not selected.")

    def setUp(self) -> None:
        self.tenant = f"pgtest-{uuid.uuid4().hex[:8]}"
        self.org = self.ws.create_org(
            tenant_id=self.tenant, name="PG Test Org", owner_user_id="owner-x", plan_id="free"
        )

    def tearDown(self) -> None:
        import psycopg

        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM org_invites WHERE org_id = %s", (self.org.id,))
            cur.execute("DELETE FROM org_members WHERE org_id = %s", (self.org.id,))
            cur.execute("DELETE FROM org_workspaces WHERE org_id = %s", (self.org.id,))
            cur.execute("DELETE FROM usage_counters WHERE tenant_id = %s", (self.tenant,))
            cur.execute("DELETE FROM organizations WHERE id = %s", (self.org.id,))
            conn.commit()

    def test_org_round_trip_and_plan_update(self) -> None:
        self.assertEqual(self.ws.get_org_by_tenant(self.tenant).id, self.org.id)
        self.ws.set_plan(self.org.id, "pro")
        self.assertEqual(self.ws.get_org(self.org.id).plan_id, "pro")

    def test_stripe_ids_persist_and_lookup(self) -> None:
        self.ws.set_stripe_ids(self.org.id, customer_id="cus_pg", subscription_id="sub_pg")
        found = self.ws.get_org_by_stripe_customer("cus_pg")
        self.assertEqual(found.id, self.org.id)
        self.assertEqual(found.stripe_subscription_id, "sub_pg")

    def test_workspace_create_and_count(self) -> None:
        w = self.ws.create_workspace(org_id=self.org.id, tenant_id=self.tenant, name="Oncology Research")
        self.assertEqual(self.ws.count_workspaces(self.org.id), 1)
        self.assertEqual(self.ws.get_workspace(w.id).slug, "oncology-research")

    def test_invite_accept_flow_is_idempotent(self) -> None:
        inv = self.ws.create_invite(
            org_id=self.org.id, email="teammate@x.dev", role="researcher", invited_by="owner-x"
        )
        member = self.ws.accept_invite(inv.token, user_id="teammate-1")
        self.assertIsNotNone(member)
        self.assertEqual(member.role, "researcher")
        # Re-accepting the same token must fail (already accepted).
        self.assertIsNone(self.ws.accept_invite(inv.token, user_id="teammate-1"))

    def test_usage_counters_increment_and_snapshot(self) -> None:
        self.assertEqual(self.us.record(self.tenant, "queries"), 1)
        self.assertEqual(self.us.record(self.tenant, "queries"), 2)
        self.assertEqual(self.us.get(self.tenant, "queries"), 2)
        self.assertEqual(self.us.snapshot(self.tenant).get("queries"), 2)

    def test_state_survives_new_store_instance(self) -> None:
        from omni_modal.saas.repository import PostgresWorkspaceStore

        self.ws.set_plan(self.org.id, "pro")
        fresh = PostgresWorkspaceStore(self.ws._pool)
        restored = fresh.get_org_by_tenant(self.tenant)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.plan_id, "pro")


if __name__ == "__main__":
    unittest.main()
