"use client";

import { formatDateTime } from "../lib/format";
import type { AuditEvent } from "../lib/api";

export interface AuditTrailCardProps {
  event: AuditEvent;
}

export default function AuditTrailCard({ event }: AuditEventCardProps) {
  const isFailed = event.event_type.toLowerCase().includes("failed");

  return (
    <div
      className={`row-card ${isFailed ? "action-card status-failed" : ""}`}
      data-testid="audit-trail-card"
      data-event-id={event.id}
    >
      <div className="meta">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <strong data-testid="event-type">{event.event_type.replace(/_/g, " ")}</strong>
          {event.actor && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 800,
                padding: "2px 8px",
                borderRadius: 99,
                background: event.actor === "agent" ? "var(--soft)" : "#e8f0fe",
                color: event.actor === "agent" ? "var(--muted)" : "#1a73e8",
                textTransform: "uppercase",
              }}
              data-testid="event-actor"
            >
              {event.actor}
            </span>
          )}
        </div>
        <small style={{ marginTop: 2 }}>{event.summary}</small>
        {(event.entity_type || event.action_id || event.agent_run_id) && (
          <small style={{ color: "var(--muted)", fontSize: 11, marginTop: 2 }}>
            {event.entity_type && <span>Target: {event.entity_type} {event.entity_id}</span>}
            {event.action_id && <span> · Action: {event.action_id}</span>}
            {event.agent_run_id && <span> · Run: {event.agent_run_id}</span>}
          </small>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
        <span className={`pill ${isFailed ? "failed" : ""}`}>
          {isFailed ? "Failed" : "Recorded"}
        </span>
        <span style={{ fontSize: 11, color: "var(--muted)", whiteSpace: "nowrap" }}>
          {formatDateTime(event.created_at)}
        </span>
      </div>
    </div>
  );
}

type AuditEventCardProps = AuditTrailCardProps;
