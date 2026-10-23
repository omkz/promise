"use client";

import Link from "next/link";
import Nav from "../../components/Nav";
import AlexaSimulator from "../../components/AlexaSimulator";

export default function AlexaPage() {
  return (
    <main className="shell">
      {/* Topbar matching PROMISE production layout */}
      <header className="topbar">
        <div className="brand-group">
          <Link href="/" className="brand-link" style={{ textDecoration: "none", color: "inherit", display: "flex", alignItems: "baseline", gap: "12px" }}>
            <div className="brand">PROMISE</div>
            <div className="tagline">Follow-through intelligence</div>
          </Link>
        </div>

        <div className="header-actions" style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <Link
            href="/"
            className="btn-secondary"
            style={{
              fontSize: "12.5px",
              padding: "6px 14px",
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              textDecoration: "none",
            }}
          >
            ← Back to Commitments
          </Link>
          <div className="status-dot">
            <span /> MCP Active
          </div>
        </div>
      </header>

      {/* Global Navigation */}
      <Nav />

      {/* Page Context Banner */}
      <div className="alexa-page-intro">
        <div className="alexa-page-intro-text">
          <div className="eyebrow" style={{ color: "#0284c7", fontWeight: 700, fontSize: "11px", letterSpacing: "0.08em" }}>
            VOICE ASSISTANT CHANNEL
          </div>
          <h1 style={{ margin: "4px 0 0", fontSize: "22px", fontWeight: 700, color: "var(--ink)", letterSpacing: "-0.02em" }}>
            Alexa+ Simulator
          </h1>
          <p style={{ margin: "6px 0 0", fontSize: "13.5px", color: "var(--muted)", maxWidth: "620px", lineHeight: "1.5" }}>
            Speak or type commitments into the simulated Alexa+ voice channel. Utterances are processed live by PROMISE via the Model Context Protocol (Streamable HTTP).
          </p>
        </div>

        <div className="alexa-page-intro-actions">
          <Link
            href="/"
            className="btn-secondary"
            style={{
              fontSize: "13px",
              padding: "8px 14px",
              textDecoration: "none",
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              whiteSpace: "nowrap",
            }}
          >
            View Commitments Dashboard &rarr;
          </Link>
        </div>
      </div>

      {/* Simulator Component */}
      <div style={{ marginTop: "16px" }}>
        <AlexaSimulator />
      </div>

      {/* Footer */}
      <footer className="footer" style={{ textAlign: "center", marginTop: "32px", color: "var(--muted)", fontSize: "13px" }}>
        PROMISE · Alexa+ Voice Channel via Model Context Protocol (SEP-1865)
      </footer>
    </main>
  );
}
