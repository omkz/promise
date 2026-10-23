"use client";

import { useEffect, useState } from "react";
import Nav from "../../components/Nav";
import ApprovalCard, { type DecisionState } from "../../components/ApprovalCard";
import { sortApprovalsNewestFirst } from "../../lib/format";
import {
  decideApproval,
  executeAction,
  getCommitments,
  listActions,
  listPendingApprovals,
  type Action,
  type Approval,
  type Commitment,
} from "../../lib/api";

// ---- top-level notice (persistent page feedback / load errors) ------------------------------

type PageNotice = {
  kind: "error" | "success" | "conflict" | "info";
  message: string;
};

function isConflictError(e: unknown): boolean {
  if (e instanceof Error) {
    const msg = e.message.toLowerCase();
    return (
      msg.includes("409") ||
      msg.includes("conflict") ||
      msg.includes("already") ||
      msg.includes("expected state") ||
      msg.includes("not in the expected state")
    );
  }
  return false;
}

// ---- page -----------------------------------------------------------------------------------

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [actions, setActions] = useState<Record<string, Action>>({});
  const [commitments, setCommitments] = useState<Record<string, Commitment>>({});
  // Per-approval decision state, keyed by approval id.
  const [states, setStates] = useState<Record<string, DecisionState>>({});
  const [loading, setLoading] = useState(true);
  const [pageNotice, setPageNotice] = useState<PageNotice | null>(null);

  // ---- data loading ------------------------------------------------------------------

  async function loadData(): Promise<void> {
    const [pending, allActions, allCommitments] = await Promise.all([
      listPendingApprovals(),
      listActions(),
      getCommitments(),
    ]);
    const sorted = sortApprovalsNewestFirst(pending);
    setApprovals(sorted);
    setActions(Object.fromEntries(allActions.map((a) => [a.id, a])));
    setCommitments(Object.fromEntries(allCommitments.map((c) => [c.id, c])));
    // Reset per-card state for cards that are no longer pending.
    setStates((prev) => {
      const stillPendingIds = new Set(pending.map((a) => a.id));
      const next: Record<string, DecisionState> = {};
      for (const id of Object.keys(prev)) {
        if (stillPendingIds.has(id)) next[id] = prev[id];
      }
      return next;
    });
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([listPendingApprovals(), listActions(), getCommitments()])
      .then(([pending, allActions, allCommitments]) => {
        if (cancelled) return;
        setApprovals(sortApprovalsNewestFirst(pending));
        setActions(Object.fromEntries(allActions.map((a) => [a.id, a])));
        setCommitments(Object.fromEntries(allCommitments.map((c) => [c.id, c])));
      })
      .catch(() => {
        if (!cancelled) {
          setPageNotice({ kind: "error", message: "Could not reach the backend — start it on port 8000." });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- per-card state helpers --------------------------------------------------------

  function setCardState(approvalId: string, next: DecisionState) {
    setStates((prev) => ({ ...prev, [approvalId]: next }));
  }

  // ---- decision handlers -------------------------------------------------------------

  async function approve(a: Approval) {
    setCardState(a.id, { type: "loading", decision: "approve" });
    setPageNotice(null);

    // Step 1: Record the approval decision.
    try {
      await decideApproval(a.id, "approved");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed";
      const conflict = isConflictError(e);
      const errorMsg = conflict
        ? "Conflict: This approval has already been decided."
        : `Approval failed: ${msg}`;
      setCardState(a.id, conflict ? { type: "conflict", message: errorMsg } : { type: "error", message: errorMsg });
      setPageNotice({ kind: conflict ? "conflict" : "error", message: errorMsg });
      await loadData().catch(() => {});
      return;
    }

    // Step 2: Approval was successfully recorded. Now execute the side-effect action.
    try {
      const result = await executeAction(a.action_id);
      const actionStatus = result?.action?.status;

      if (actionStatus === "executed") {
        setPageNotice({
          kind: "success",
          message: "Approval recorded and action executed successfully.",
        });
        setCardState(a.id, { type: "success", message: "Approved and executed." });
      } else if (actionStatus === "failed") {
        const errorDetail = result?.action?.error ? `: ${result.action.error}` : "";
        setPageNotice({
          kind: "error",
          message: `Approval recorded, but PROMISE recorded execution as failed${errorDetail}. Please verify with the external provider before retrying.`,
        });
        setCardState(a.id, {
          type: "error",
          message: `Approval recorded, but execution recorded as failed${errorDetail}`,
        });
      } else {
        const statusDetail = actionStatus ? ` (status: ${actionStatus})` : "";
        setPageNotice({
          kind: "error",
          message: `Approval recorded, but the execution outcome could not be confirmed${statusDetail}. Please check Activity and the external provider before retrying.`,
        });
        setCardState(a.id, {
          type: "error",
          message: `Approval recorded, but execution outcome could not be confirmed${statusDetail}`,
        });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Execution request failed";
      // Distinguish HTTP/network request errors from backend-reported execution failures.
      // External side effects might have been accepted before the timeout or network drop.
      setPageNotice({
        kind: "error",
        message: `Approval recorded, but action execution request failed: ${msg}. The execution outcome could not be confirmed. Please check Activity and the external provider before retrying.`,
      });
      setCardState(a.id, {
        type: "error",
        message: `Approval recorded, but execution outcome could not be confirmed (${msg})`,
      });
    } finally {
      // Refresh after decision attempt — the pending approval row disappears,
      // but pageNotice ensures the user has persistent feedback.
      await loadData().catch(() => {});
    }
  }

  async function reject(a: Approval) {
    setCardState(a.id, { type: "loading", decision: "reject" });
    setPageNotice(null);

    try {
      await decideApproval(a.id, "rejected");
      setPageNotice({
        kind: "success",
        message: "Decision recorded: Action rejected. No external action will be executed.",
      });
      setCardState(a.id, { type: "success", message: "Rejected." });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed";
      const conflict = isConflictError(e);
      const errorMsg = conflict
        ? "Conflict: This approval has already been decided."
        : `Rejection failed: ${msg}`;
      setCardState(a.id, conflict ? { type: "conflict", message: errorMsg } : { type: "error", message: errorMsg });
      setPageNotice({ kind: conflict ? "conflict" : "error", message: errorMsg });
    } finally {
      await loadData().catch(() => {});
    }
  }

  // ---- render ------------------------------------------------------------------------

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <div className="brand">PROMISE</div>
          <div className="tagline">Your AI follow-through agent.</div>
        </div>
      </header>
      <Nav />
      <div className="page-head">
        <p className="eyebrow">EXTERNAL SIDE EFFECTS WAIT HERE</p>
        <h1>Pending approvals</h1>
      </div>

      {pageNotice && (
        <div
          className={`notice${
            pageNotice.kind === "error"
              ? " notice--error"
              : pageNotice.kind === "success"
                ? " notice--success"
                : pageNotice.kind === "conflict"
                  ? " notice--warning"
                  : ""
          }`}
          role={pageNotice.kind === "error" ? "alert" : "status"}
          data-testid="page-notice"
        >
          {pageNotice.message}
        </div>
      )}

      <div className="rows" data-testid="approvals-list">
        {loading && <div className="empty" data-testid="loading-indicator">Loading approvals…</div>}
        {!loading && approvals.length === 0 && (
          <div className="empty" data-testid="empty-state">Nothing waiting on you right now.</div>
        )}

        {approvals.map((a) => {
          const action = actions[a.action_id];
          const commitment = action?.commitment_id ? commitments[action.commitment_id] : undefined;
          const cardState: DecisionState = states[a.id] ?? { type: "idle" };
          return (
            <ApprovalCard
              key={a.id}
              approval={a}
              action={action}
              commitment={commitment}
              state={cardState}
              onApprove={approve}
              onReject={reject}
            />
          );
        })}
      </div>
    </main>
  );
}
