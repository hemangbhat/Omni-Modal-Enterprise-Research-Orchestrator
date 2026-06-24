/**
 * Typed client for the SaaS endpoints (workspaces, usage, billing, team,
 * notifications, admin). Wraps the instrumented `apiRequest` and reads the
 * active session token + backend URL from `getClientApiConfig`.
 *
 * Every call surfaces backend plan-limit responses (HTTP 402) as a typed
 * `PlanLimitError` so the UI can show an upgrade prompt instead of a generic
 * failure.
 */

import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";

export type Plan = {
  id: string;
  name: string;
  price_usd_month: number;
  max_workspaces: number;
  max_members: number;
  monthly_query_limit: number;
  monthly_upload_limit: number;
  storage_mb: number;
  features: string[];
};

export type Organization = {
  id: string;
  tenant_id: string;
  name: string;
  plan_id: string;
  owner_user_id: string;
  created_at: number;
};

export type Workspace = {
  id: string;
  org_id: string;
  tenant_id: string;
  name: string;
  slug: string;
  created_at: number;
};

export type Member = {
  org_id: string;
  user_id: string;
  email: string;
  role: string;
  status: string;
  created_at: number;
};

export type Invite = {
  id: string;
  org_id: string;
  email: string;
  role: string;
  status: string;
  created_at: number;
  expires_at: number;
  invited_by: string;
  expired: boolean;
  /** Present only on the creation response. */
  token?: string;
  accept_url?: string;
};

export type UsageMetric = {
  used: number;
  limit: number;
  unlimited: boolean;
  percent: number;
};

export type UsageReport = {
  plan: Plan;
  metrics: Record<string, UsageMetric>;
};

export type Notification = {
  id: string;
  tenant_id: string;
  user_id: string | null;
  kind: "info" | "success" | "warning" | "error";
  title: string;
  body: string;
  read: boolean;
  created_at: number;
};

export type AdminStats = {
  organization: Organization;
  members: number;
  workspaces: number;
  usage: Record<string, number>;
  event_counts: Record<string, number>;
  adapters: { storage: string; email: string; analytics: string };
  audit_events: number;
  billing_mode: string;
};

export class PlanLimitError extends Error {
  metric: string;
  limit: number;
  constructor(message: string, metric: string, limit: number) {
    super(message);
    this.name = "PlanLimitError";
    this.metric = metric;
    this.limit = limit;
  }
}

function authHeaders(): Record<string, string> {
  const { token } = getClientApiConfig();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function getJson<T>(path: string): Promise<T> {
  const { baseUrl } = getClientApiConfig();
  const res = await apiRequest(path, { method: "GET", headers: authHeaders() }, { baseUrl });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const { baseUrl } = getClientApiConfig();
  const res = await apiRequest(
    path,
    { method: "POST", headers: authHeaders(), body: JSON.stringify(body) },
    { baseUrl }
  );
  if (res.status === 402) {
    const data = (await res.json().catch(() => ({}))) as {
      error?: string;
      metric?: string;
      limit?: number;
    };
    throw new PlanLimitError(
      data.error ?? "Plan limit reached.",
      data.metric ?? "unknown",
      data.limit ?? 0
    );
  }
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

// ── Workspaces ──────────────────────────────────────────────────────────
export function listWorkspaces() {
  return getJson<{ organization: Organization; workspaces: Workspace[]; total: number }>(
    "/workspaces"
  );
}

export function createWorkspace(name: string) {
  return postJson<Workspace>("/workspaces", { name });
}

// ── Usage & billing ─────────────────────────────────────────────────────
export function getUsage() {
  return getJson<UsageReport>("/usage");
}

export function getBilling() {
  return getJson<{
    billing_mode: string;
    current_plan: string;
    plans: Plan[];
    usage: UsageReport;
  }>("/billing");
}

export function changePlan(planId: string) {
  return postJson<{ organization: Organization; billing_mode: string }>(
    "/billing/change-plan",
    { plan_id: planId }
  );
}

// ── Stripe billing ──────────────────────────────────────────────────────
export function startCheckout(planId: string) {
  return postJson<{ url: string; session_id: string }>("/billing/checkout", {
    plan_id: planId
  });
}

export function confirmCheckout(sessionId: string) {
  return postJson<{ paid: boolean; plan_id: string | null }>("/billing/confirm", {
    session_id: sessionId
  });
}

export function openBillingPortal() {
  return postJson<{ url: string }>("/billing/portal", {});
}

// ── Team ────────────────────────────────────────────────────────────────
export function listMembers() {
  return getJson<{ members: Member[]; invites: Invite[]; total: number }>("/members");
}

export function inviteMember(email: string, role: string) {
  return postJson<Invite>("/invites", { email, role });
}

export type InvitePreview = {
  email: string;
  role: string;
  status: string;
  expired: boolean;
  organization: string;
  valid: boolean;
};

export function previewInvite(token: string) {
  return getJson<InvitePreview>(`/invites/preview?token=${encodeURIComponent(token)}`);
}

export function acceptInvite(token: string) {
  return postJson<Member>("/invites/accept", { token });
}

// ── Notifications ───────────────────────────────────────────────────────
export function listNotifications() {
  return getJson<{ notifications: Notification[]; unread: number; total: number }>(
    "/notifications"
  );
}

export function markNotificationRead(id: string) {
  return postJson<{ marked_read: number }>("/notifications/read", { id });
}

export function markAllNotificationsRead() {
  return postJson<{ marked_read: number }>("/notifications/read", { all: true });
}

// ── Admin ───────────────────────────────────────────────────────────────
export function getAdminStats() {
  return getJson<AdminStats>("/admin/stats");
}
