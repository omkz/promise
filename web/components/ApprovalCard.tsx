"use client";

import { formatPayloadKey, formatPayloadValue } from "../lib/format";
import type { Action, Approval, Commitment } from "../lib/api";

// ---- per-card decision state -----------------------------------------------------------------

export type DecisionState =
  | { type: "idle" }
  | { type: "loading"; decision: "approve" | "reject" }
  | { type: "success"; message: string }
  | { type: "conflict"; message: string }
  | { type: "error"; message: string };

// ---- props -----------------------------------------------------------------------------------

export interface ApprovalCardProps {
  approval: Approval;
  action: Action | undefined;
  commitment: Commitment | undefined;
  /** Decision state owned by the parent; drives loading / result UI. */
  state: DecisionState;
  onApprove: (approval: Approval) => void;
  onReject: (approval: Approval) => void;
}

// ---- helpers ---------------------------------------------------------------------------------

function actionLabel(type: string): string {
  return type.replace(/_/g, " ");
}

// ---- component -------------------------------------------------------------------------------

export default function ApprovalCard({
  approval,
  action,
  commitment,
  state,
  onApprove,
  onReject,
}: ApprovalCardProps) {
  const busy = state.type === "loading";
  const payloadEntries = action ? Object.entries(action.payload) : [];

  return (
    <div className="row-card stacked" data-testid="approval-card" data-approval-id={approval.id}>
      {/* ── header row: action label + status pill ──────────────────────────── */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16 }}>
        <div className="meta">
          <strong data-testid="action-label">
            {action ? actionLabel(action.type) : approval.action_id}
          </strong>
          <small>
            {commitment ? commitment.title : "Unknown commitment"}
            {" · Requested "}
            {new Date(approval.requested_at).toLocaleString()}
          </small>
        </div>
        <span className={`pill ${approval.status}`}>{approval.status}</span>
      </div>

      {/* ── commitment context quote ─────────────────────────────────────────── */}
      {commitment?.description && (
        <div className="quote">
          <span>Commitment</span>
          <p>&ldquo;{commitment.description}&rdquo;</p>
        </div>
      )}

      {/* ── action payload detail ────────────────────────────────────────────── */}
      {payloadEntries.length > 0 && (
        <div className="steps-detail">
          {payloadEntries.map(([key, value]) => (
            <div className="step-row" key={key}>
              <span>{formatPayloadKey(key)}</span>
              <span>{formatPayloadValue(value)}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── per-card result banner ───────────────────────────────────────────── */}
      {state.type === "success" && (
        <div className="card-notice card-notice--success" data-testid="card-notice-success" role="status">
          {state.message}
        </div>
      )}
      {state.type === "conflict" && (
        <div className="card-notice card-notice--conflict" data-testid="card-notice-conflict" role="status">
          {state.message}
        </div>
      )}
      {state.type === "error" && (
        <div className="card-notice card-notice--error" data-testid="card-notice-error" role="alert">
          {state.message}
        </div>
      )}

      {/* ── approve / reject buttons ─────────────────────────────────────────── */}
      <div className="row-actions">
        <button
          className="ghost"
          disabled={busy}
          aria-busy={busy && state.decision === "reject"}
          data-testid="btn-reject"
          onClick={() => onReject(approval)}
        >
          {busy && state.decision === "reject" ? "Rejecting…" : "Reject"}
        </button>
        <button
          disabled={busy}
          aria-busy={busy && state.decision === "approve"}
          data-testid="btn-approve"
          onClick={() => onApprove(approval)}
        >
          {busy && state.decision === "approve" ? "Approving…" : "Approve & execute"}
        </button>
      </div>
    </div>
  );
}
