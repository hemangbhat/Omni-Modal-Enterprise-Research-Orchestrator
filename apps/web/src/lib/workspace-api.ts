/**
 * Client for workspace-wide views: the dashboard, global search, billing
 * history and the admin list of failed ingestion jobs.
 */
import { apiRequest } from "@/lib/api-client";
import { parseApiError } from "@/lib/api-error";
import type { DocumentRecord, JobRecord } from "@/lib/documents-api";
import { getClientApiConfig } from "@/lib/env";
import type { ActivityEntry } from "@/lib/projects-api";
import type { SessionSummary } from "@/lib/research-api";
import type { UsageReport } from "@/lib/saas-api";

export type Dashboard = {
  documents: { ready: number; processing: number; uploaded: number; failed: number; archived: number; total: number };
  projects: number;
  recent_documents: DocumentRecord[];
  recent_sessions: SessionSummary[];
  recent_activity: (ActivityEntry & { project_id: string; project_name: string | null })[];
  usage: UsageReport;
};

export type SearchResults = {
  documents: { id: string; title: string; status: string }[];
  projects: { id: string; name: string }[];
  sessions: { id: string; title: string }[];
  scope: string;
};

export type Invoice = {
  id: string;
  number: string | null;
  status: string | null;
  currency: string | null;
  amount_due: number | null;
  amount_paid: number | null;
  created: number | null;
  hosted_invoice_url: string | null;
  invoice_pdf: string | null;
};

export type Failure = { document: DocumentRecord; job: JobRecord | null };

async function call<T>(path: string, method = "GET"): Promise<T> {
  const { baseUrl, token } = getClientApiConfig();
  const res = await apiRequest(
    path,
    { method, headers: token ? { Authorization: `Bearer ${token}` } : {} },
    { baseUrl }
  );
  if (!res.ok) {
    const { message } = parseApiError(await res.json().catch(() => ({})));
    throw new Error(message ?? `The server returned an error (HTTP ${res.status}).`);
  }
  return (await res.json()) as T;
}

export const getDashboard = () => call<Dashboard>("/dashboard");
export const searchWorkspace = (q: string) => call<SearchResults>(`/search?q=${encodeURIComponent(q)}`);
export const getInvoices = () => call<{ available: boolean; invoices: Invoice[] }>("/billing/invoices");
export const getFailures = () => call<{ failures: Failure[] }>("/admin/ingestion/failures");
export const retryFailure = (documentId: string) =>
  call<{ job: JobRecord }>(`/admin/ingestion/failures/${encodeURIComponent(documentId)}/retry`, "POST");

export function formatMoney(cents: number | null, currency: string | null): string {
  if (cents == null) return "—";
  try {
    return new Intl.NumberFormat([], { style: "currency", currency: (currency ?? "usd").toUpperCase() }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${(currency ?? "").toUpperCase()}`;
  }
}
