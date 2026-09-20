"use client";

import { useEffect, useState } from "react";
import Nav from "../components/Nav";
import {
  createCommitment,
  decideApproval,
  executeAction,
  getCommitments,
  handleCommitment,
  type Commitment,
  type HandleResult,
} from "../lib/api";

const example = "I'll send Andi the revised proposal tomorrow morning.";

function formatDue(value: string | null) {
  if (!value) return "No deadline";
  return new Date(value).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function Home() {
  const [commitments, setCommitments] = useState<Commitment[]>([]);
  const [selected, setSelected] = useState<Commitment | null>(null);
  const [input, setInput] = useState("");
  const [agent, setAgent] = useState<HandleResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function refresh() {
    setCommitments(await getCommitments());
  }

  useEffect(() => { refresh().catch(() => setNotice("Start the backend on port 8000.")); }, []);

  async function detect() {
    if (!input.trim()) return;
    setBusy(true); setNotice("");
    try { await createCommitment(input.trim()); setInput(""); await refresh(); setNotice("Commitment detected and remembered."); }
    catch (e) { setNotice(e instanceof Error ? e.message : "Something went wrong"); }
    finally { setBusy(false); }
  }

  async function handle() {
    if (!selected) return;
    setBusy(true); setNotice(""); setAgent(null);
    try { const result = await handleCommitment(selected.id); setAgent(result); await refresh(); }
    catch (e) { setNotice(e instanceof Error ? e.message : "Agent failed"); }
    finally { setBusy(false); }
  }

  async function approveAndExecute() {
    if (!agent) return;
    setBusy(true);
    try {
      await decideApproval(agent.approval.id, "approved");
      await executeAction(agent.action.id);
      setNotice("Approved and sent. Commitment completed.");
      setAgent(null); setSelected(null); await refresh();
    } catch (e) { setNotice(e instanceof Error ? e.message : "Execution failed"); }
    finally { setBusy(false); }
  }

  async function reject() {
    if (!agent) return;
    setBusy(true);
    try { await decideApproval(agent.approval.id, "rejected"); setNotice("Rejected. Nothing was sent."); setAgent(null); await refresh(); }
    catch (e) { setNotice(e instanceof Error ? e.message : "Failed to reject"); }
    finally { setBusy(false); }
  }

  const open = commitments.filter((x) => !["completed", "cancelled", "failed"].includes(x.status));

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <div className="brand">PROMISE</div>
          <div className="tagline">Your AI follow-through agent.</div>
        </div>
        <div className="status-dot"><span /> Dev workspace</div>
      </header>
      <Nav />

      <section className="hero">
        <div>
          <p className="eyebrow">DETECT · REMEMBER · ACT</p>
          <h1>Remember what you said you&apos;d do.</h1>
          <p className="sub">Turn everyday commitments into work an agent can help finish.</p>
        </div>
        <div className="capture-card">
          <label>Say a commitment</label>
          <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder={example} />
          <button onClick={detect} disabled={busy}>Detect commitment</button>
          <button className="ghost" onClick={() => setInput(example)}>Use demo sentence</button>
        </div>
      </section>

      {notice && <div className="notice">{notice}</div>}

      <section className="grid">
        <div className="panel">
          <div className="panel-head"><div><p className="eyebrow">OPEN COMMITMENTS</p><h2>Your promises</h2></div><span className="count">{open.length}</span></div>
          <div className="list">
            {open.map((c) => (
              <button key={c.id} className={`commitment ${selected?.id === c.id ? "selected" : ""}`} onClick={() => { setSelected(c); setAgent(null); }}>
                <span className={`priority ${c.priority}`} />
                <span className="commitment-copy"><strong>{c.title}</strong><small>{c.description}</small></span>
                <span className="due">{formatDue(c.due_at)}</span>
              </button>
            ))}
            {open.length === 0 && <div className="empty">No open commitments yet.</div>}
          </div>
        </div>

        <div className="panel detail">
          {!selected ? (
            <div className="empty large"><div className="spark">✦</div><h3>Select a commitment</h3><p>Pick one from the left to inspect its evidence and ask the agent to handle it.</p></div>
          ) : !agent ? (
            <>
              <p className="eyebrow">COMMITMENT DETAIL</p>
              <h2>{selected.title}</h2>
              <div className="detail-grid">
                <div><span>Due</span><strong>{formatDue(selected.due_at)}</strong></div>
                <div><span>Status</span><strong>{selected.status}</strong></div>
                <div><span>Priority</span><strong>{selected.priority}</strong></div>
                <div><span>Confidence</span><strong>{Math.round(selected.confidence * 100)}%</strong></div>
              </div>
              <div className="quote"><span>Source</span><p>“{selected.description}”</p></div>
              <button onClick={handle} disabled={busy}>Handle this</button>
            </>
          ) : (
            <>
              <p className="eyebrow">AGENT EXECUTION — WAITING FOR APPROVAL</p>
              <h2>Ready for your approval.</h2>
              <div className="steps">
                <div className="step done">✓ Found {agent.document.name}</div>
                {agent.changes.map((x) => <div className="step done" key={x}>✓ {x}</div>)}
                <div className="step done">✓ Draft prepared for {agent.draft.recipient}</div>
              </div>
              <div className="draft"><span>MESSAGE</span><pre>{agent.draft.body}</pre></div>
              <div className="actions">
                <button className="ghost" onClick={reject} disabled={busy}>Reject</button>
                <button onClick={approveAndExecute} disabled={busy}>Approve & send</button>
              </div>
            </>
          )}
        </div>
      </section>

      <footer>PROMISE · Alexa+ / MCP-ready · Dev workspace</footer>
    </main>
  );
}
