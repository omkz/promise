"use client";

import { useState } from "react";
import { formatActionType, formatDateTime, formatPayloadKey, formatPayloadValue } from "../lib/format";
import type { Action, Commitment } from "../lib/api";

export interface ActionHistoryCardProps {
  action: Action;
  commitment?: Commitment;
}

export default function ActionHistoryCard({ action, commitment }: ActionHistoryCardProps) {
  const [showDetails, setShowDetails] = useState(false);

  const isFailed = action.status === "failed" || !!action.error;
  const isRejected = action.status === "rejected";
  const isApproved = action.status === "approved";
  const isWaiting = action.status === "waiting_for_approval";
  const isExecuted = action.status === "executed";

  const statusClass = isFailed
    ? "status-failed"
    : isRejected
      ? "status-rejected"
      : isApproved
        ? "status-approved"
        : isExecuted
          ? "status-executed"
          : isWaiting
            ? "status-waiting_for_approval"
            : "";

  const payloadEntries = action.payload ? Object.entries(action.payload) : [];
  const timestamp = action.executed_at ?? action.updated_at ?? action.created_at;

  return (
    <div
      className={`row-card stacked action-card ${statusClass}`}
      data-testid="action-history-card"
      data-action-id={action.id}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16 }}>
        <div className="meta">
          <strong data-testid="action-title">{formatActionType(action.type)}</strong>
          <small>
            {commitment ? commitment.title : (action.commitment_id || "Workspace action")}
            {timestamp ? ` · ${formatDateTime(timestamp)}` : ""}
          </small>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className={`pill ${action.status}`} data-testid="action-status">
            {action.status.replace(/_/g, " ")}
          </span>
          <button
            type="button"
            className="card-toggle-btn"
            aria-expanded={showDetails}
            onClick={() => setShowDetails(!showDetails)}
            data-testid="toggle-action-details"
          >
            {showDetails ? "Hide details ▲" : "Details ▼"}
          </button>
        </div>
      </div>

      {commitment?.description && (
        <div className="quote">
          <span>Commitment</span>
          <p>&ldquo;{commitment.description}&rdquo;</p>
        </div>
      )}

      {isFailed && (
        <div className="card-notice card-notice--error" data-testid="action-error" role="alert">
          Action execution failed: {action.error || "An error occurred during execution."}
        </div>
      )}

      {isRejected && (
        <div className="card-notice card-notice--conflict" data-testid="action-decision-rejected">
          Approval decision: Rejected by user
        </div>
      )}

      {isApproved && (
        <div className="card-notice card-notice--success" data-testid="action-decision-approved">
          Approval decision: Approved by user
        </div>
      )}

      {isWaiting && (
        <div className="card-notice card-notice--conflict" data-testid="action-decision-waiting">
          Awaiting user approval before external side effect
        </div>
      )}

      {showDetails && (
        <div className="steps-detail" data-testid="action-payload-details">
          {payloadEntries.length > 0 && (
            <div>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 800,
                  color: "var(--muted)",
                  textTransform: "uppercase",
                  letterSpacing: ".08em",
                  display: "block",
                  marginBottom: 6,
                }}
              >
                Payload parameters
              </span>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {payloadEntries.map(([key, value]) => (
                  <div className="step-row" key={key}>
                    <span>{formatPayloadKey(key)}</span>
                    <span>{formatPayloadValue(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {action.result && (
            <div style={{ marginTop: 6 }}>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 800,
                  color: "var(--muted)",
                  textTransform: "uppercase",
                  letterSpacing: ".08em",
                  display: "block",
                  marginBottom: 6,
                }}
              >
                Execution result
              </span>
              <div className="step-row" style={{ flexDirection: "column", gap: 4 }}>
                <pre
                  style={{
                    margin: 0,
                    whiteSpace: "pre-wrap",
                    fontFamily: "ui-monospace, monospace",
                    fontSize: 12,
                    color: "var(--ink)",
                  }}
                >
                  {JSON.stringify(action.result, null, 2)}
                </pre>
              </div>
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
            <span>Action ID: {action.id}</span>
            {action.idempotency_key && <span>Idempotency: {action.idempotency_key}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
