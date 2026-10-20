"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Nav from "../../components/Nav";
import {
  connectIntegration,
  disconnectIntegration,
  listIntegrations,
  startCalendarConnect,
  startGmailConnect,
  type IntegrationAccount,
} from "../../lib/api";

type PageNotice = {
  kind: "error" | "success" | "info";
  message: string;
};

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
  const [actionNotice, setActionNotice] = useState<PageNotice | null | "dismissed">(null);
  const searchParams = useSearchParams();

  // Derive notice from OAuth callback redirect (?gmail=connected|error, ?calendar=connected|error)
  const oauthNotice: PageNotice | null = (() => {
    const gmail = searchParams.get("gmail");
    const calendar = searchParams.get("calendar");
    const reason = searchParams.get("reason");

    if (gmail === "connected") {
      return { kind: "success", message: "Gmail connected successfully." };
    }
    if (gmail === "error") {
      const msg = reason === "denied"
        ? "Gmail authorization was cancelled or access was denied."
        : "Couldn't connect Gmail. Please try again.";
      return { kind: "error", message: msg };
    }
    if (calendar === "connected") {
      return { kind: "success", message: "Google Calendar connected successfully." };
    }
    if (calendar === "error") {
      const msg = reason === "denied"
        ? "Google Calendar authorization was cancelled or access was denied."
        : "Couldn't connect Google Calendar. Please try again.";
      return { kind: "error", message: msg };
    }
    return null;
  })();

  const notice = actionNotice === "dismissed" ? null : (actionNotice ?? oauthNotice);

  async function refresh() {
    try {
      const data = await listIntegrations();
      setAccounts(data);
    } catch {
      // Ignore background refresh errors
    }
  }

  useEffect(() => {
    let cancelled = false;
    listIntegrations()
      .then((data) => {
        if (!cancelled) setAccounts(data);
      })
      .catch(() => {
        if (!cancelled) {
          setActionNotice({ kind: "error", message: "Could not reach the backend — start it on port 8000." });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function connectGmail() {
    setBusy(true);
    setActionNotice("dismissed");
    try {
      const { authorization_url } = await startGmailConnect();
      window.location.href = authorization_url;
    } catch (e) {
      setActionNotice({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to start Gmail connect",
      });
      setBusy(false);
    }
  }

  async function connectCalendar() {
    setBusy(true);
    setActionNotice("dismissed");
    try {
      const { authorization_url } = await startCalendarConnect();
      window.location.href = authorization_url;
    } catch (e) {
      setActionNotice({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to start Google Calendar connect",
      });
      setBusy(false);
    }
  }

  async function connectDemoAccount() {
    if (!identifier.trim()) return;
    setBusy(true);
    setActionNotice("dismissed");
    try {
      await connectIntegration(provider, identifier.trim());
      setIdentifier("");
      setActionNotice({ kind: "success", message: "Demo integration metadata recorded." });
      await refresh();
    } catch (e) {
      setActionNotice({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to connect demo integration",
      });
    } finally {
      setBusy(false);
    }
  }

  async function disconnect(id: string) {
    setBusy(true);
    setActionNotice("dismissed");
    try {
      await disconnectIntegration(id);
      setActionNotice({ kind: "info", message: "Account disconnected." });
      await refresh();
    } catch (e) {
      setActionNotice({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to disconnect account",
      });
    } finally {
      setBusy(false);
    }
  }

  const gmailAccount = accounts.find((a) => a.provider === "gmail" && a.status !== "disconnected");
  const calendarAccount = accounts.find((a) => a.provider === "google_calendar" && a.status !== "disconnected");
  const calendarNeedsPermission = !calendarAccount && !!gmailAccount;
  const otherAccounts = accounts.filter((a) => a.provider !== "gmail" && a.provider !== "google_calendar");

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
        <p className="eyebrow">WHERE THE AGENT LOOKS AND ACTS</p>
        <h1>Connections</h1>
      </div>

      {notice && (
        <div
          className={`notice${notice.kind === "error" ? " notice--error" : notice.kind === "success" ? " notice--success" : ""}`}
          role={notice.kind === "error" ? "alert" : "status"}
          data-testid="page-notice"
        >
          {notice.message}
        </div>
      )}

      {loading ? (
        <div className="empty" data-testid="loading-indicator">
          Loading connections…
        </div>
      ) : (
        <>
          <div className="section-head">
            <p className="eyebrow">PRIMARY OAUTH INTEGRATIONS</p>
            <h2>Verified providers</h2>
          </div>

          <div className="rows" style={{ marginBottom: 36 }}>
            {/* Gmail OAuth card */}
            <div className="row-card" data-testid="gmail-card">
              <div className="meta">
                <strong>Gmail</strong>
                <small>
                  {gmailAccount ? `Connected as: ${gmailAccount.account_identifier}` : "Not connected"}
                </small>
              </div>
              {gmailAccount && (
                <span className={`pill ${gmailAccount.status}`} data-testid="gmail-status">
                  {gmailAccount.status}
                </span>
              )}
              <div className="row-actions">
                {gmailAccount ? (
                  <button
                    className="ghost"
                    disabled={busy}
                    onClick={() => disconnect(gmailAccount.id)}
                    data-testid="btn-disconnect-gmail"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    disabled={busy}
                    onClick={connectGmail}
                    data-testid="btn-connect-gmail"
                  >
                    Connect Gmail
                  </button>
                )}
              </div>
            </div>

            {/* Google Calendar OAuth card */}
            <div className="row-card" data-testid="calendar-card">
              <div className="meta">
                <strong>Google Calendar</strong>
                <small>
                  {calendarAccount
                    ? `Connected as: ${calendarAccount.account_identifier}`
                    : calendarNeedsPermission
                      ? "Permission required — Google session active, calendar scope needed"
                      : "Not connected"}
                </small>
              </div>
              {calendarAccount ? (
                <span className={`pill ${calendarAccount.status}`} data-testid="calendar-status">
                  {calendarAccount.status}
                </span>
              ) : calendarNeedsPermission ? (
                <span className="pill pending" data-testid="calendar-status">
                  Permission needed
                </span>
              ) : null}
              <div className="row-actions">
                {calendarAccount ? (
                  <button
                    className="ghost"
                    disabled={busy}
                    onClick={() => disconnect(calendarAccount.id)}
                    data-testid="btn-disconnect-calendar"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    disabled={busy}
                    onClick={connectCalendar}
                    data-testid="btn-connect-calendar"
                  >
                    {calendarNeedsPermission ? "Authorize Calendar" : "Connect Calendar"}
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Explicit Simulated / Demo Section */}
          <div className="section-head">
            <p className="eyebrow">LOCAL / TESTING ONLY</p>
            <h2>Simulated integrations</h2>
          </div>

          <p style={{ color: "var(--muted)", fontSize: 13, lineHeight: 1.5, margin: "-4px 0 16px" }}>
            The providers below (Google Drive, Slack, Notion) do not execute live OAuth flows.
            They register local test metadata accounts used by PROMISE to test context retrieval
            and draft planning against mock/seed data.
          </p>

          <div className="connect-form" data-testid="demo-connect-form">
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              aria-label="Select test provider"
              data-testid="demo-provider-select"
            >
              <option value="google_drive">Google Drive (Demo)</option>
              <option value="slack">Slack (Demo)</option>
              <option value="notion">Notion (Demo)</option>
            </select>
            <input
              placeholder="Test account identifier (e.g. name@company.com)"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              aria-label="Test account identifier"
              data-testid="demo-identifier-input"
            />
            <button
              onClick={connectDemoAccount}
              disabled={busy || !identifier.trim()}
              data-testid="btn-add-demo"
            >
              Add test metadata
            </button>
          </div>

          <div className="rows" data-testid="demo-accounts-list">
            {otherAccounts.length === 0 && (
              <div className="empty" data-testid="empty-demo-accounts">
                No simulated test accounts recorded yet.
              </div>
            )}
            {otherAccounts.map((a) => (
              <div className="row-card" key={a.id} data-testid="demo-account-card">
                <div className="meta">
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <strong>{a.provider.replace(/_/g, " ")}</strong>
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 800,
                        padding: "2px 6px",
                        borderRadius: 6,
                        background: "var(--soft)",
                        color: "var(--muted)",
                        textTransform: "uppercase",
                      }}
                    >
                      Demo Metadata
                    </span>
                  </div>
                  <small>{a.account_identifier}</small>
                </div>
                <span className={`pill ${a.status}`}>{a.status}</span>
                <div className="row-actions">
                  <button
                    className="ghost"
                    disabled={busy || a.status === "disconnected"}
                    onClick={() => disconnect(a.id)}
                    data-testid={`btn-disconnect-${a.id}`}
                  >
                    Disconnect
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </main>
  );
}
