"""Tests for the workspace/org/member/invite store (saas/workspaces.py)."""
from __future__ import annotations

import unittest

import _path  # noqa: F401

from omni_modal.saas.workspaces import WorkspaceStore


class TestWorkspaceStore(unittest.TestCase):
    def setUp(self) -> None:
        self.store = WorkspaceStore()

    def test_create_and_get_org_by_tenant(self) -> None:
        org = self.store.create_org(tenant_id="t1", name="Acme", owner_user_id="u1")
        self.assertEqual(self.store.get_org_by_tenant("t1").id, org.id)
        self.assertEqual(self.store.get_org(org.id).name, "Acme")

    def test_create_workspace_and_count(self) -> None:
        org = self.store.create_org(tenant_id="t1", name="Acme", owner_user_id="u1")
        self.store.create_workspace(org_id=org.id, tenant_id="t1", name="Research")
        self.store.create_workspace(org_id=org.id, tenant_id="t1", name="Trials")
        self.assertEqual(self.store.count_workspaces(org.id), 2)
        names = {w.name for w in self.store.list_workspaces(org.id)}
        self.assertEqual(names, {"Research", "Trials"})

    def test_workspace_slug_generated(self) -> None:
        org = self.store.create_org(tenant_id="t1", name="Acme", owner_user_id="u1")
        ws = self.store.create_workspace(org_id=org.id, tenant_id="t1", name="Oncology Research!")
        self.assertEqual(ws.slug, "oncology-research")

    def test_members(self) -> None:
        org = self.store.create_org(tenant_id="t1", name="Acme", owner_user_id="u1")
        self.store.add_member(org_id=org.id, user_id="u1", email="a@x.com", role="admin")
        self.store.add_member(org_id=org.id, user_id="u2", email="b@x.com", role="researcher")
        self.assertEqual(self.store.count_members(org.id), 2)
        self.assertTrue(self.store.remove_member(org.id, "u2"))
        self.assertEqual(self.store.count_members(org.id), 1)
        self.assertFalse(self.store.remove_member(org.id, "missing"))

    def test_invite_lifecycle(self) -> None:
        org = self.store.create_org(tenant_id="t1", name="Acme", owner_user_id="u1")
        invite = self.store.create_invite(
            org_id=org.id, email="New@X.com", role="researcher", invited_by="u1"
        )
        self.assertEqual(invite.status, "pending")
        self.assertEqual(invite.email, "new@x.com")  # normalized
        self.assertFalse(invite.is_expired)

        # Accept by token -> creates active membership
        member = self.store.accept_invite(invite.token, user_id="u2")
        self.assertIsNotNone(member)
        self.assertEqual(member.role, "researcher")
        self.assertEqual(self.store.get_invite(invite.id).status, "accepted")

        # Re-accepting the same token fails (already accepted)
        self.assertIsNone(self.store.accept_invite(invite.token, user_id="u3"))

    def test_invite_token_is_not_leaked_by_default(self) -> None:
        org = self.store.create_org(tenant_id="t1", name="Acme", owner_user_id="u1")
        invite = self.store.create_invite(org_id=org.id, email="x@x.com", role="researcher", invited_by="u1")
        self.assertNotIn("token", invite.to_dict())
        self.assertIn("token", invite.to_dict(include_token=True))

    def test_revoke_invite(self) -> None:
        org = self.store.create_org(tenant_id="t1", name="Acme", owner_user_id="u1")
        invite = self.store.create_invite(org_id=org.id, email="x@x.com", role="researcher", invited_by="u1")
        self.assertTrue(self.store.revoke_invite(invite.id))
        self.assertIsNone(self.store.accept_invite(invite.token, user_id="u9"))
        self.assertFalse(self.store.revoke_invite(invite.id))  # already revoked

    def test_set_plan(self) -> None:
        org = self.store.create_org(tenant_id="t1", name="Acme", owner_user_id="u1")
        self.store.set_plan(org.id, "pro")
        self.assertEqual(self.store.get_org(org.id).plan_id, "pro")


if __name__ == "__main__":
    unittest.main()
