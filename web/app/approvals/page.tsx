"use client";

import { useEffect, useState } from "react";
import Nav from "../../components/Nav";
import { decideApproval, executeAction, listActions, listPendingApprovals, type Action, type Approval } from "../../lib/api";

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [actions, setActions] = useState<Record<string, Action>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  async function refresh() {
    const [pending, allActions] = await Promise.all([listPendingApprovals(), listActions()]);
    setApprovals(pending);
    setActions(Object.fromEntries(allActions.map((a) => [a.id, a])));
  }

  useEffect(() => { refresh().catch(() => setNotice("Start the backend on port 8000.")); }, []);

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
        {approvals.length === 0 && <div className="empty">Nothing waiting on you right now.</div>}
        {approvals.map((a) => {
          const action = actions[a.action_id];
          return (
            <div className="row-card" key={a.id}>
              <div className="meta">
                <strong>{action ? action.type.replace(/_/g, " ") : a.action_id}</strong>
                <small>Requested {new Date(a.requested_at).toLocaleString()}</small>
              </div>
              <span className={`pill ${a.status}`}>{a.status}</span>
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
