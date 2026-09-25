"use client";

import { useEffect, useState } from "react";
import Nav from "../../components/Nav";
import { getAgentRun, listAgentRuns, listAuditEvents, type AgentRun, type AgentStep, type AuditEvent } from "../../lib/api";

export default function ActivityPage() {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    Promise.all([listAgentRuns(), listAuditEvents()])
      .then(([r, e]) => { setRuns(r); setEvents(e); })
      .catch(() => setNotice("Start the backend on port 8000."))
      .finally(() => setLoading(false));
  }, []);

  async function toggle(run: AgentRun) {
    if (openId === run.id) { setOpenId(null); return; }
    try {
      const detail = await getAgentRun(run.id);
      setSteps(detail.steps);
      setOpenId(run.id);
    } catch (e) { setNotice(e instanceof Error ? e.message : "Failed to load run steps"); }
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
        {loading && <div className="empty">Loading agent runs…</div>}
        {!loading && runs.length === 0 && <div className="empty">No agent runs yet — try &quot;Handle this&quot; on a commitment.</div>}
        {runs.map((r) => (
          <div key={r.id}>
            <button className="row-card" style={{ width: "100%", textAlign: "left" }} onClick={() => toggle(r)}>
              <div className="meta">
                <strong>{r.trigger}</strong>
                <small>
                  Started {new Date(r.started_at).toLocaleString()}
                  {r.ended_at ? ` · Ended ${new Date(r.ended_at).toLocaleString()}` : " · In progress"}
                </small>
              </div>
              <span className={`pill ${r.status}`}>{r.status.replace(/_/g, " ")}</span>
            </button>
            {openId === r.id && (
              <div className="steps-detail">
                {steps.length === 0 && <div className="empty">No steps recorded for this run.</div>}
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

      <div className="section-head"><p className="eyebrow">EVERYTHING THAT HAPPENED</p><h2>Audit trail</h2></div>
      <div className="rows">
        {loading && <div className="empty">Loading audit events…</div>}
        {!loading && events.length === 0 && <div className="empty">No audit events recorded yet.</div>}
        {events.map((e) => (
          <div className="row-card" key={e.id}>
            <div className="meta">
              <strong>{e.event_type.replace(/_/g, " ")}</strong>
              <small>{e.summary}</small>
            </div>
            <span className="pill">{new Date(e.created_at).toLocaleString()}</span>
          </div>
        ))}
      </div>
    </main>
  );
}
