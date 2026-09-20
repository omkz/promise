"use client";

import { useEffect, useState } from "react";
import Nav from "../../components/Nav";
import { connectIntegration, disconnectIntegration, listIntegrations, type IntegrationAccount } from "../../lib/api";

export default function ConnectionsPage() {
  const [accounts, setAccounts] = useState<IntegrationAccount[]>([]);
  const [provider, setProvider] = useState("gmail");
  const [identifier, setIdentifier] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function refresh() {
    setAccounts(await listIntegrations());
  }

  useEffect(() => { refresh().catch(() => setNotice("Start the backend on port 8000.")); }, []);

  async function connect() {
    if (!identifier.trim()) return;
    setBusy(true); setNotice("");
    try { await connectIntegration(provider, identifier.trim()); setIdentifier(""); await refresh(); }
    catch (e) { setNotice(e instanceof Error ? e.message : "Failed to connect"); }
    finally { setBusy(false); }
  }

  async function disconnect(id: string) {
    setBusy(true);
    try { await disconnectIntegration(id); await refresh(); }
    catch (e) { setNotice(e instanceof Error ? e.message : "Failed to disconnect"); }
    finally { setBusy(false); }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div><div className="brand">PROMISE</div><div className="tagline">Your AI follow-through agent.</div></div>
      </header>
      <Nav />
      <div className="page-head"><p className="eyebrow">WHERE THE AGENT LOOKS AND ACTS</p><h1>Connections</h1></div>
      {notice && <div className="notice">{notice}</div>}

      <div className="connect-form">
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="gmail">Gmail</option>
          <option value="google_drive">Google Drive</option>
          <option value="slack">Slack</option>
          <option value="notion">Notion</option>
          <option value="calendar">Calendar</option>
        </select>
        <input placeholder="Account (e.g. name@company.com)" value={identifier} onChange={(e) => setIdentifier(e.target.value)} />
        <button onClick={connect} disabled={busy}>Connect</button>
      </div>

      <div className="rows">
        {accounts.length === 0 && <div className="empty">No connected accounts yet. Local/demo data is used until you connect one.</div>}
        {accounts.map((a) => (
          <div className="row-card" key={a.id}>
            <div className="meta"><strong>{a.provider}</strong><small>{a.account_identifier}</small></div>
            <span className={`pill ${a.status}`}>{a.status}</span>
            <div className="row-actions">
              <button className="ghost" disabled={busy || a.status === "disconnected"} onClick={() => disconnect(a.id)}>Disconnect</button>
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
