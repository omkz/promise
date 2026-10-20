"use client";

import { useEffect, useState } from "react";
import Nav from "../../components/Nav";
import ApprovalCard, { type DecisionState } from "../../components/ApprovalCard";
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

// ---- top-level notice (load errors / backend down) ------------------------------------------

type PageNotice = { kind: "error"; message: string } | { kind: "info"; message: string };

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
    setApprovals(pending);
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
        setApprovals(pending);
        setActions(Object.fromEntries(allActions.map((a) => [a.id, a])));
        setCommitments(Object.fromEntries(allCommitments.map((c) => [c.id, c])));
      })
      .catch(() => {
        if (!cancelled) setPageNotice({ kind: "error", message: "Could not reach the backend — start it on port 8000." });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  // ---- per-card state helpers --------------------------------------------------------

  function setCardState(approvalId: string, next: DecisionState) {
    setStates((prev) => ({ ...prev, [approvalId]: next }));
  }

  // ---- decision handlers -------------------------------------------------------------

  async function approve(a: Approval) {
    setCardState(a.id, { type: "loading", decision: "approve" });
    try {
      await decideApproval(a.id, "approved");
      await executeAction(a.action_id);
      setCardState(a.id, { type: "success", message: "Approved and executed." });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed";
      const isConflict = msg.includes("409") || msg.toLowerCase().includes("conflict") || msg.toLowerCase().includes("already");
      setCardState(a.id, isConflict ? { type: "conflict", message: "Already decided — refreshing…" } : { type: "error", message: msg });
    } finally {
      // Refresh after every decision attempt — including failures and 409 conflicts —
      // so this row disappears (or shows updated status) instead of remaining stale.
      await loadData().catch(() => {});
    }
  }

  async function reject(a: Approval) {
    setCardState(a.id, { type: "loading", decision: "reject" });
    try {
      await decideApproval(a.id, "rejected");
      setCardState(a.id, { type: "success", message: "Rejected." });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed";
      const isConflict = msg.includes("409") || msg.toLowerCase().includes("conflict") || msg.toLowerCase().includes("already");
      setCardState(a.id, isConflict ? { type: "conflict", message: "Already decided — refreshing…" } : { type: "error", message: msg });
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
          className={`notice${pageNotice.kind === "error" ? " notice--error" : ""}`}
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
