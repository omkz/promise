import { authHeaders } from "./auth";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "content-type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export type Commitment = {
  id: string;
  title: string;
  description: string;
  due_at: string | null;
  status: string;
  priority: string;
  contact_id?: string | null;
  confidence: number;
};

export type Contact = { id: string; name: string; email: string | null };

export type Document = { id: string; name: string; content_text: string };

export type Draft = { id: string; recipient: string; subject: string; body: string; status: string };

export type Action = { id: string; type: string; status: string; commitment_id: string | null; payload: Record<string, unknown> };

export type Approval = { id: string; action_id: string; status: string; requested_at: string; decided_by: string | null };

export type AgentRun = { id: string; commitment_id: string | null; trigger: string; status: string; started_at: string; ended_at: string | null };

export type AgentStep = { id: string; name: string; status: string; output_summary: string | null; started_at: string };

export type AuditEvent = { id: string; event_type: string; summary: string; created_at: string };

export type IntegrationAccount = { id: string; provider: string; account_identifier: string; status: string };

export type HandleResult = {
  agent_run: AgentRun;
  commitment: Commitment;
  contact: Contact | null;
  document: Document;
  changes: string[];
  draft: Draft;
  action: Action;
  approval: Approval;
};

export const getCommitments = (query = "", status?: string) =>
  request<Commitment[]>(`/api/commitments?query=${encodeURIComponent(query)}${status ? `&status=${status}` : ""}`);

export const getCommitment = (id: string) => request<Commitment>(`/api/commitments/${id}`);

export type CreateCommitmentResult = {
  detected: boolean;
  persisted: boolean;
  needs_confirmation: boolean;
  commitment: Commitment | null;
  contact: Contact | null;
  reason?: string | null;
  message?: string | null;
};

export const createCommitment = (text: string, source_system: "web" | "alexa" = "web", confirm = false) =>
  request<CreateCommitmentResult>(`/api/commitments`, {
    method: "POST",
    body: JSON.stringify({ text, source_system, confirm }),
  });

export const handleCommitment = (commitmentId: string) =>
  request<HandleResult>(`/api/commitments/${commitmentId}/handle`, { method: "POST" });

export const decideApproval = (approvalId: string, decision: "approved" | "rejected", note?: string) =>
  request<Approval>(`/api/approvals/${approvalId}/decide`, { method: "POST", body: JSON.stringify({ decision, note }) });

export const executeAction = (actionId: string) =>
  request<{ action: Action; commitment: Commitment | null }>(`/api/actions/${actionId}/execute`, { method: "POST" });

export const listPendingApprovals = () => request<Approval[]>(`/api/approvals/pending`);

export const listActions = (commitmentId?: string) =>
  request<Action[]>(`/api/actions${commitmentId ? `?commitment_id=${commitmentId}` : ""}`);

export const listAgentRuns = () => request<AgentRun[]>(`/api/agent-runs`);

export const getAgentRun = (id: string) => request<{ run: AgentRun; steps: AgentStep[] }>(`/api/agent-runs/${id}`);

export const listAuditEvents = () => request<AuditEvent[]>(`/api/audit-events`);

export const listIntegrations = () => request<IntegrationAccount[]>(`/api/integrations`);

export const connectIntegration = (provider: string, account_identifier: string) =>
  request<IntegrationAccount>(`/api/integrations`, { method: "POST", body: JSON.stringify({ provider, account_identifier }) });

export const disconnectIntegration = (id: string) =>
  request<IntegrationAccount>(`/api/integrations/${id}/disconnect`, { method: "POST" });

// Gmail OAuth kickoff: fetched (with the usual auth headers) rather than a plain link,
// since a browser's top-level navigation to a link can never carry X-Dev-User-Id /
// Authorization headers -- see services/api/promise_api/routers/integrations.py's
// gmail_connect docstring. The caller navigates the browser itself once this resolves.
export const startGmailConnect = () => request<{ authorization_url: string }>(`/api/integrations/gmail/connect`);

// Google Calendar OAuth kickoff -- same reasoning as startGmailConnect above. A separate
// flow/redirect URI from Gmail's, so connecting Calendar never forces a Gmail reconnect.
export const startCalendarConnect = () => request<{ authorization_url: string }>(`/api/integrations/calendar/connect`);

export type CalendarEvent = {
  id: string;
  summary: string;
  description: string;
  start_at: string | null;
  end_at: string | null;
  location: string | null;
  attendees: string[];
  html_link: string | null;
};

export const searchCalendarEvents = (query = "", time_min?: string, time_max?: string) =>
  request<CalendarEvent[]>(
    `/api/calendar/events?query=${encodeURIComponent(query)}` +
      (time_min ? `&time_min=${encodeURIComponent(time_min)}` : "") +
      (time_max ? `&time_max=${encodeURIComponent(time_max)}` : "")
  );
