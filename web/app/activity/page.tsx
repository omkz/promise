"use client";

import { useEffect, useState } from "react";
import Nav from "../../components/Nav";
import { getAgentRun, listAgentRuns, type AgentRun, type AgentStep } from "../../lib/api";

export default function ActivityPage() {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [notice, setNotice] = useState("");

  useEffect(() => { listAgentRuns().then(setRuns).catch(() => setNotice("Start the backend on port 8000.")); }, []);

  async function toggle(run: AgentRun) {
    if (openId === run.id) { setOpenId(null); return; }
    const detail = await getAgentRun(run.id);
    setSteps(detail.steps);
    setOpenId(run.id);
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div><div className="brand">PROMISE</div><div className="tagline">Your AI follow-through agent.</div></div>
      </header>
      <Nav />
      <div className="page-head"><p className="eyebrow">EVERY TRIGGER, EVERY STEP</p><h1>Agent activity</h1></div>
      {notice && <div className="notice">{notice}</div>}

      <div className="rows">
        {runs.length === 0 && <div className="empty">No agent runs yet — try &quot;Handle this&quot; on a commitment.</div>}
        {runs.map((r) => (
          <div key={r.id}>
            <button className="row-card" style={{ width: "100%", textAlign: "left" }} onClick={() => toggle(r)}>
              <div className="meta">
                <strong>{r.trigger}</strong>
                <small>Started {new Date(r.started_at).toLocaleString()}</small>
              </div>
              <span className={`pill ${r.status}`}>{r.status.replace(/_/g, " ")}</span>
            </button>
            {openId === r.id && (
              <div className="steps-detail">
                {steps.map((s) => (
                  <div className="step-row" key={s.id}>
                    <span>{s.name}</span>
                    <span>{s.output_summary ?? s.status}</span>
                    <span className={`pill ${s.status}`}>{s.status}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </main>
  );
}
