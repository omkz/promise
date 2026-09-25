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
