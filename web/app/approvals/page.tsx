"use client";

import { useEffect, useState } from "react";
import Nav from "../../components/Nav";
import {
  decideApproval, executeAction, getCommitments, listActions, listPendingApprovals,
  type Action, type Approval, type Commitment,
} from "../../lib/api";
import { formatPayloadKey, formatPayloadValue } from "../../lib/format";

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [actions, setActions] = useState<Record<string, Action>>({});
  const [commitments, setCommitments] = useState<Record<string, Commitment>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");

  async function refresh() {
    const [pending, allActions, allCommitments] = await Promise.all([listPendingApprovals(), listActions(), getCommitments()]);
    setApprovals(pending);
    setActions(Object.fromEntries(allActions.map((a) => [a.id, a])));
    setCommitments(Object.fromEntries(allCommitments.map((c) => [c.id, c])));
  }

  useEffect(() => {
    Promise.all([listPendingApprovals(), listActions(), getCommitments()])
      .then(([pending, allActions, allCommitments]) => {
        setApprovals(pending);
        setActions(Object.fromEntries(allActions.map((a) => [a.id, a])));
        setCommitments(Object.fromEntries(allCommitments.map((c) => [c.id, c])));
      })
      .catch(() => setNotice("Start the backend on port 8000."))
      .finally(() => setLoading(false));
  }, []);

  async function approve(a: Approval) {
    setBusyId(a.id); setNotice("");
    try {
      await decideApproval(a.id, "approved");
      await executeAction(a.action_id);
      setNotice("Approved and executed.");
      await refresh();
    } catch (e) { setNotice(e instanceof Error ? e.message : "Failed"); }
    finally { setBusyId(null); }
  }

  async function reject(a: Approval) {
    setBusyId(a.id); setNotice("");
    try { await decideApproval(a.id, "rejected"); setNotice("Rejected."); await refresh(); }
    catch (e) { setNotice(e instanceof Error ? e.message : "Failed"); }
    finally { setBusyId(null); }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div><div className="brand">PROMISE</div><div className="tagline">Your AI follow-through agent.</div></div>
      </header>
      <Nav />
      <div className="page-head"><p className="eyebrow">EXTERNAL SIDE EFFECTS WAIT HERE</p><h1>Pending approvals</h1></div>
      {notice && <div className="notice">{notice}</div>}

      <div className="rows">
        {loading && <div className="empty">Loading approvals…</div>}
        {!loading && approvals.length === 0 && <div className="empty">Nothing waiting on you right now.</div>}
        {approvals.map((a) => {
          const action = actions[a.action_id];
          const commitment = action?.commitment_id ? commitments[action.commitment_id] : undefined;
          const payloadEntries = action ? Object.entries(action.payload) : [];
          return (
            <div className="row-card stacked" key={a.id}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16 }}>
                <div className="meta">
                  <strong>{action ? action.type.replace(/_/g, " ") : a.action_id}</strong>
                  <small>
                    {commitment ? commitment.title : "Unknown commitment"} · Requested{" "}
                    {new Date(a.requested_at).toLocaleString()}
                  </small>
                </div>
                <span className={`pill ${a.status}`}>{a.status}</span>
              </div>

              {commitment?.description && (
                <div className="quote">
                  <span>Commitment</span>
                  <p>&ldquo;{commitment.description}&rdquo;</p>
                </div>
              )}

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

              <div className="row-actions">
                <button className="ghost" disabled={busyId === a.id} onClick={() => reject(a)}>Reject</button>
                <button disabled={busyId === a.id} onClick={() => approve(a)}>Approve & execute</button>
              </div>
            </div>
          );
        })}
      </div>
    </main>
  );
}
