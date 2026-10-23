// Small, generic display-only helpers shared by pages that render a proposed/executed
// Action's `payload` (a provider-specific dict — e.g. Gmail's draft_id/document_id vs.
// Calendar's title/start_at/end_at/attendees) without hard-coding either shape here.

export function formatPayloadKey(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatPayloadValue(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "No deadline";
  return new Date(value).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function formatActionType(type: string): string {
  return type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatDuration(startedAt: string, endedAt: string | null | undefined): string {
  if (!endedAt) return "In progress";
  const start = new Date(startedAt).getTime();
  const end = new Date(endedAt).getTime();
  const diffMs = Math.max(0, end - start);
  if (diffMs < 1000) return `${diffMs}ms`;
  return `${(diffMs / 1000).toFixed(1)}s`;
}

export function getActionTimestamp(action: {
  executed_at?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
}): number {
  const ts = action.executed_at ?? action.updated_at ?? action.created_at;
  if (!ts) return 0;
  const time = new Date(ts).getTime();
  return Number.isNaN(time) ? 0 : time;
}

export function sortActionsNewestFirst<T extends {
  id: string;
  executed_at?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
}>(actions: T[]): T[] {
  return [...actions].sort((a, b) => {
    const timeA = getActionTimestamp(a);
    const timeB = getActionTimestamp(b);
    if (timeA !== timeB) {
      return timeB - timeA;
    }
    return b.id.localeCompare(a.id);
  });
}

export function sortApprovalsNewestFirst<T extends {
  id: string;
  requested_at: string;
}>(approvals: T[]): T[] {
  return [...approvals].sort((a, b) => {
    const timeA = new Date(a.requested_at).getTime();
    const timeB = new Date(b.requested_at).getTime();
    if (timeA !== timeB) {
      return timeB - timeA;
    }
    return b.id.localeCompare(a.id);
  });
}
