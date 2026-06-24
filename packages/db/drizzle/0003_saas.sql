-- Phase B: persist the SaaS domain so multi-tenant state survives restart.
-- Ids are stored as text (app-generated UUIDv4) and timestamps as epoch
-- doubles to mirror the in-memory dataclasses exactly, so the Postgres-backed
-- store is a drop-in replacement for the in-memory one.

CREATE TABLE IF NOT EXISTS organizations (
  id text PRIMARY KEY,
  tenant_id varchar(128) NOT NULL UNIQUE,
  name text NOT NULL,
  plan_id varchar(64) NOT NULL DEFAULT 'free',
  owner_user_id varchar(128) NOT NULL,
  stripe_customer_id text,
  stripe_subscription_id text,
  created_at double precision NOT NULL
);

CREATE INDEX IF NOT EXISTS organizations_stripe_customer_idx
  ON organizations (stripe_customer_id);

CREATE TABLE IF NOT EXISTS org_workspaces (
  id text PRIMARY KEY,
  org_id text NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  tenant_id varchar(128) NOT NULL,
  name text NOT NULL,
  slug varchar(160) NOT NULL,
  created_at double precision NOT NULL
);

CREATE INDEX IF NOT EXISTS org_workspaces_org_idx ON org_workspaces (org_id);
CREATE INDEX IF NOT EXISTS org_workspaces_tenant_idx ON org_workspaces (tenant_id);

CREATE TABLE IF NOT EXISTS org_members (
  org_id text NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id varchar(128) NOT NULL,
  email varchar(320) NOT NULL,
  role varchar(80) NOT NULL,
  status varchar(32) NOT NULL DEFAULT 'active',
  created_at double precision NOT NULL,
  PRIMARY KEY (org_id, user_id)
);

CREATE INDEX IF NOT EXISTS org_members_org_idx ON org_members (org_id);

CREATE TABLE IF NOT EXISTS org_invites (
  id text PRIMARY KEY,
  org_id text NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  email varchar(320) NOT NULL,
  role varchar(80) NOT NULL,
  token text NOT NULL UNIQUE,
  status varchar(32) NOT NULL DEFAULT 'pending',
  invited_by varchar(128) NOT NULL,
  created_at double precision NOT NULL,
  expires_at double precision NOT NULL
);

CREATE INDEX IF NOT EXISTS org_invites_org_idx ON org_invites (org_id);
CREATE INDEX IF NOT EXISTS org_invites_token_idx ON org_invites (token);

CREATE TABLE IF NOT EXISTS usage_counters (
  tenant_id varchar(128) NOT NULL,
  period varchar(16) NOT NULL,         -- e.g. "2025-06"
  metric varchar(64) NOT NULL,         -- queries | uploads | ...
  count integer NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, period, metric)
);

CREATE TABLE IF NOT EXISTS document_workspace (
  document_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  tenant_id varchar(128) NOT NULL,
  created_at double precision NOT NULL
);

CREATE INDEX IF NOT EXISTS document_workspace_ws_idx ON document_workspace (workspace_id);
