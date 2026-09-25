"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Nav from "../../components/Nav";
import {
  connectIntegration, disconnectIntegration, listIntegrations, startCalendarConnect, startGmailConnect,
  type IntegrationAccount,
} from "../../lib/api";

// useSearchParams() (to read ?gmail=connected|error off the OAuth callback's redirect)
// requires a Suspense boundary around any Client Component that calls it, or a static
// production build fails -- see the useSearchParams docs' "Missing Suspense boundary".
export default function ConnectionsPage() {
  return (
    <Suspense fallback={null}>
      <ConnectionsPageContent />
    </Suspense>
  );
}

function ConnectionsPageContent() {
  const [accounts, setAccounts] = useState<IntegrationAccount[]>([]);
  const [provider, setProvider] = useState("google_drive");
  const [identifier, setIdentifier] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const searchParams = useSearchParams();

  async function refresh() {
    setAccounts(await listIntegrations());
  }

  useEffect(() => {
    listIntegrations()
      .then(setAccounts)
      .catch(() => setNotice("Start the backend on port 8000."))
      .finally(() => setLoading(false));
  }, []);

  // Landed back here from GET /api/integrations/gmail|calendar/callback's redirect --
  // ?gmail=connected|error / ?calendar=connected|error, never a token (see those
  // routes' own docstrings). Deriving the toast from the URL after an OAuth redirect has
  // no render-time equivalent (it's a one-time reaction to navigation, not a value to
  // compute from current props/state), so this is the documented exception to
  // react-hooks/set-state-in-effect rather than something to restructure around.
  useEffect(() => {
    const gmail = searchParams.get("gmail");
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (gmail === "connected") { setNotice("Gmail connected."); refresh(); }
    else if (gmail === "error") { setNotice("Couldn't connect Gmail. Please try again."); }
    const calendar = searchParams.get("calendar");
    if (calendar === "connected") { setNotice("Google Calendar connected."); refresh(); }
    else if (calendar === "error") { setNotice("Couldn't connect Google Calendar. Please try again."); }
  }, [searchParams]);

  async function connectGmail() {
    setBusy(true); setNotice("");
    try {
      const { authorization_url } = await startGmailConnect();
      window.location.href = authorization_url;
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Failed to start Gmail connect");
      setBusy(false);
    }
  }

  async function connectCalendar() {
    setBusy(true); setNotice("");
    try {
      const { authorization_url } = await startCalendarConnect();
      window.location.href = authorization_url;
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Failed to start Google Calendar connect");
      setBusy(false);
    }
  }

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

  const gmailAccount = accounts.find((a) => a.provider === "gmail" && a.status !== "disconnected");
  const calendarAccount = accounts.find((a) => a.provider === "google_calendar" && a.status !== "disconnected");
  // "Permission required": the user has already granted Google access via Gmail but
  // hasn't yet granted the separate Calendar scope -- distinct from "Not connected"
  // (never connected any Google account at all), per the connections UI spec.
  const calendarNeedsPermission = !calendarAccount && !!gmailAccount;
  const otherAccounts = accounts.filter((a) => a.provider !== "gmail" && a.provider !== "google_calendar");

  return (
    <main className="shell">
      <header className="topbar">
        <div><div className="brand">PROMISE</div><div className="tagline">Your AI follow-through agent.</div></div>
      </header>
      <Nav />
      <div className="page-head"><p className="eyebrow">WHERE THE AGENT LOOKS AND ACTS</p><h1>Connections</h1></div>
      {notice && <div className="notice">{notice}</div>}

      {loading ? (
        <div className="empty">Loading connections…</div>
      ) : (
        <>
          <div className="row-card">
            <div className="meta">
              <strong>Gmail</strong>
              <small>{gmailAccount ? `Connected as: ${gmailAccount.account_identifier}` : "Not connected"}</small>
            </div>
            {gmailAccount && <span className={`pill ${gmailAccount.status}`}>{gmailAccount.status}</span>}
            <div className="row-actions">
              {gmailAccount ? (
                <button className="ghost" disabled={busy} onClick={() => disconnect(gmailAccount.id)}>Disconnect</button>
              ) : (
                <button disabled={busy} onClick={connectGmail}>Connect Gmail</button>
              )}
            </div>
          </div>

          <div className="row-card">
            <div className="meta">
              <strong>Google Calendar</strong>
              <small>
                {calendarAccount
                  ? `Connected as: ${calendarAccount.account_identifier}`
                  : calendarNeedsPermission
                    ? "Permission required"
                    : "Not connected"}
              </small>
            </div>
            {calendarAccount && <span className={`pill ${calendarAccount.status}`}>{calendarAccount.status}</span>}
            <div className="row-actions">
              {calendarAccount ? (
                <button className="ghost" disabled={busy} onClick={() => disconnect(calendarAccount.id)}>Disconnect</button>
              ) : (
                <button disabled={busy} onClick={connectCalendar}>Connect Calendar</button>
              )}
            </div>
          </div>

          <div className="connect-form">
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="google_drive">Google Drive</option>
              <option value="slack">Slack</option>
              <option value="notion">Notion</option>
            </select>
            <input placeholder="Account (e.g. name@company.com)" value={identifier} onChange={(e) => setIdentifier(e.target.value)} />
            <button onClick={connect} disabled={busy}>Connect</button>
          </div>

          <div className="rows">
            {otherAccounts.length === 0 && <div className="empty">No other connected accounts yet. Local/demo data is used until you connect one.</div>}
            {otherAccounts.map((a) => (
              <div className="row-card" key={a.id}>
                <div className="meta"><strong>{a.provider}</strong><small>{a.account_identifier}</small></div>
                <span className={`pill ${a.status}`}>{a.status}</span>
                <div className="row-actions">
                  <button className="ghost" disabled={busy || a.status === "disconnected"} onClick={() => disconnect(a.id)}>Disconnect</button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </main>
  );
}
