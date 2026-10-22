"use client";

import { useEffect, useState, useCallback } from "react";
import Nav from "../components/Nav";
import AskPromiseAI from "../components/AskPromiseAI";
import {
  decideApproval,
  executeAction,
  getCommitments,
  handleCommitment,
  listActions,
  listPendingApprovals,
  type Action,
  type Approval,
  type Commitment,
  type HandleResult,
} from "../lib/api";
import { formatDateTime, formatPayloadKey, formatPayloadValue } from "../lib/format";

type FilterType = "all" | "waiting_for_approval" | "open";

export default function Home() {
  const [commitments, setCommitments] = useState<Commitment[]>([]);
  const [selected, setSelected] = useState<Commitment | null>(null);
  const [selectedAction, setSelectedAction] = useState<Action | null>(null);
  const [selectedApproval, setSelectedApproval] = useState<Approval | null>(null);
  const [agent, setAgent] = useState<HandleResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterType>("all");

  const refresh = useCallback(async () => {
    try {
      const data = await getCommitments();
      setCommitments(data);
      if (selected) {
        const updated = data.find((c) => c.id === selected.id);
        if (updated) setSelected(updated);
      }
    } catch {
      setNotice("Unable to reach the PROMISE service on port 8000.");
    }
  }, [selected]);

  useEffect(() => {
    let cancelled = false;
    getCommitments()
      .then((data) => {
        if (!cancelled) {
          setCommitments(data);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setNotice("Unable to reach the PROMISE service on port 8000.");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // When a commitment in waiting_for_approval is selected, fetch linked pending action and approval
  useEffect(() => {
    let cancelled = false;
    if (selected && selected.status === "waiting_for_approval" && !agent) {
      Promise.all([listActions(selected.id), listPendingApprovals()])
        .then(([actions, approvals]) => {
          if (cancelled) return;
          const act = actions.find((a) => a.commitment_id === selected.id);
          setSelectedAction(act || null);
          if (act) {
            const appr = approvals.find((ap) => ap.action_id === act.id);
            setSelectedApproval(appr || null);
          } else {
            setSelectedApproval(null);
          }
        })
        .catch(() => {
          if (cancelled) return;
          setSelectedAction(null);
          setSelectedApproval(null);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [selected, agent]);

  async function handle() {
    if (!selected) return;
    setBusy(true);
    setNotice(null);
    setAgent(null);
    try {
      const result = await handleCommitment(selected.id);
      setAgent(result);
      await refresh();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Failed to plan follow-through");
    } finally {
      setBusy(false);
    }
  }

  // Active action/approval to display (either from recent handleCommitment or loaded from database)
  const activeApproval = agent?.approval || selectedApproval;
  const activeAction = agent?.action || selectedAction;
  const activeDraft = agent?.draft || (activeAction?.payload?.draft as Record<string, unknown> | undefined);

  async function approveAndExecute() {
    const approvalId = activeApproval?.id;
    const actionId = activeAction?.id;
    if (!approvalId || !actionId) return;

    setBusy(true);
    try {
      await decideApproval(approvalId, "approved");
      await executeAction(actionId);
      setNotice("Action approved and executed successfully.");
      setAgent(null);
      await refresh();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Execution failed");
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    const approvalId = activeApproval?.id;
    if (!approvalId) return;

    setBusy(true);
    try {
      await decideApproval(approvalId, "rejected");
      setNotice("Action rejected. Nothing was sent.");
      setAgent(null);
      await refresh();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Failed to reject action");
    } finally {
      setBusy(false);
    }
  }

  // Filter commitments
  const filteredCommitments = commitments.filter((c) => {
    if (filter === "waiting_for_approval") return c.status === "waiting_for_approval";
    if (filter === "open") return c.status === "open";
    return !["completed", "cancelled", "failed"].includes(c.status);
  });

  const waitingApprovalCount = commitments.filter((c) => c.status === "waiting_for_approval").length;
  const openCount = commitments.filter((c) => c.status === "open").length;

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand-group">
          <div className="brand">PROMISE</div>
          <div className="tagline">Follow-through intelligence</div>
        </div>
        <div className="status-dot">
          <span /> Workspace active
        </div>
      </header>

      <Nav />

      {/* Unified Primary Natural-Language Input */}
      <AskPromiseAI onCommitmentCreated={refresh} />

      {notice && (
        <div className="status-banner status-banner--processing" style={{ marginBottom: "16px" }} role="status">
          <span>{notice}</span>
          <button type="button" className="banner-dismiss" onClick={() => setNotice(null)}>✕</button>
        </div>
      )}

      {/* Commitments & Follow-Through Workspace */}
      <section className="grid">
        {/* Left Column: Commitments List */}
        <div className="panel">
          <div className="panel-head">
            <div className="panel-title-group">
              <h2 className="panel-title">Commitments</h2>
              <span className="count">{filteredCommitments.length}</span>
            </div>

            <div className="filter-tabs" role="tablist" aria-label="Commitment filters">
              <button
                type="button"
                className={`filter-tab ${filter === "all" ? "active" : ""}`}
                onClick={() => setFilter("all")}
              >
                All
              </button>
              <button
                type="button"
                className={`filter-tab ${filter === "waiting_for_approval" ? "active" : ""}`}
                onClick={() => setFilter("waiting_for_approval")}
              >
                Needs review {waitingApprovalCount > 0 ? `(${waitingApprovalCount})` : ""}
              </button>
              <button
                type="button"
                className={`filter-tab ${filter === "open" ? "active" : ""}`}
                onClick={() => setFilter("open")}
              >
                Open {openCount > 0 ? `(${openCount})` : ""}
              </button>
            </div>
          </div>

          <div className="list" role="list">
            {filteredCommitments.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`commitment ${selected?.id === c.id ? "selected" : ""}`}
                onClick={() => {
                  setSelected(c);
                  setAgent(null);
                  setSelectedAction(null);
                  setSelectedApproval(null);
                }}
              >
                <span className={`priority ${c.priority}`} />
                <span className="commitment-copy">
                  <strong>{c.title}</strong>
                  <small>{c.description}</small>
                </span>
                <div className="commitment-meta-right">
                  <span className={`pill ${c.status}`}>
                    {c.status === "waiting_for_approval"
                      ? "Needs review"
                      : c.status === "open"
                      ? "Open"
                      : c.status}
                  </span>
                  <span className="due">{formatDateTime(c.due_at)}</span>
                </div>
              </button>
            ))}

            {loading && (
              <div className="empty-state">
                <p>Loading commitments…</p>
              </div>
            )}

            {!loading && filteredCommitments.length === 0 && (
              <div className="empty-state">
                <div className="empty-icon">✓</div>
                <h3>No commitments found</h3>
                <p>
                  {filter === "all"
                    ? "You are all caught up. Type a commitment above to start tracking."
                    : "No commitments match this filter."}
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Commitment & Follow-Through Detail */}
        <div className="panel detail">
          {!selected ? (
            <div className="empty-state" style={{ minHeight: "420px" }}>
              <div className="empty-icon">📋</div>
              <h3>Select a commitment</h3>
              <p>Choose an item from the list to view its evidence, linked context, and follow-through actions.</p>
            </div>
          ) : (
            <>
              <div className="detail-header">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className={`pill ${selected.status}`}>
                    {selected.status === "waiting_for_approval"
                      ? "Needs review"
                      : selected.status === "open"
                      ? "Open"
                      : selected.status}
                  </span>
                  <span className="due">Due {formatDateTime(selected.due_at)}</span>
                </div>
                <h2 className="detail-title">{selected.title}</h2>
              </div>

              <div className="detail-meta-grid">
                <div className="detail-meta-item">
                  <span className="detail-meta-label">Priority</span>
                  <span className="detail-meta-value" style={{ textTransform: "capitalize" }}>{selected.priority}</span>
                </div>
                <div className="detail-meta-item">
                  <span className="detail-meta-label">Status</span>
                  <span className="detail-meta-value" style={{ textTransform: "capitalize" }}>{selected.status.replace(/_/g, " ")}</span>
                </div>
                <div className="detail-meta-item">
                  <span className="detail-meta-label">Confidence</span>
                  <span className="detail-meta-value">{Math.round(selected.confidence * 100)}%</span>
                </div>
              </div>

              <div className="quote-card">
                <span className="quote-card-label">Original Statement</span>
                <p>“{selected.description}”</p>
              </div>

              {/* Action Planning & Follow-Through Safeguard View */}
              {(selected.status === "waiting_for_approval" || agent) && activeAction ? (
                <div className="action-preview-card">
                  <div className="action-preview-title">
                    <span>Proposed Follow-Through Action</span>
                    <span className="pill waiting_for_approval">Waiting Approval</span>
                  </div>

                  {agent?.draft || activeDraft ? (
                    <div className="draft-preview">
                      <div className="draft-field">
                        <span className="draft-field-label">To:</span>
                        <span className="draft-field-value">{agent?.draft?.recipient || String(activeDraft?.recipient || "Contact")}</span>
                      </div>
                      <div className="draft-field">
                        <span className="draft-field-label">Subject:</span>
                        <span className="draft-field-value">{agent?.draft?.subject || String(activeDraft?.subject || selected.title)}</span>
                      </div>
                      <div className="draft-body">
                        {agent?.draft?.body || String(activeDraft?.body || "")}
                      </div>
                    </div>
                  ) : (
                    <div className="steps-detail">
                      {Object.entries(activeAction.payload || {}).map(([key, value]) => (
                        <div className="step-row" key={key}>
                          <span style={{ fontWeight: 600, color: "var(--muted)" }}>{formatPayloadKey(key)}</span>
                          <span>{formatPayloadValue(value)}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="approval-safeguard-banner" style={{ marginTop: "14px" }}>
                    <span>🔒 External action requires your approval before anything is sent.</span>
                  </div>

                  <div className="action-buttons">
                    <button
                      type="button"
                      className="btn-danger"
                      onClick={reject}
                      disabled={busy}
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={approveAndExecute}
                      disabled={busy}
                    >
                      {agent?.draft || activeDraft ? "Approve & send" : "Approve action"}
                    </button>
                  </div>
                </div>
              ) : selected.status === "open" ? (
                <div style={{ marginTop: "auto", paddingTop: "16px" }}>
                  <button
                    type="button"
                    className="btn-primary"
                    style={{ width: "100%", padding: "10px" }}
                    onClick={handle}
                    disabled={busy}
                  >
                    {busy ? "Planning follow-through…" : "Plan follow-through with AI"}
                  </button>
                </div>
              ) : (
                <div style={{ marginTop: "auto", paddingTop: "16px", color: "var(--muted)", fontSize: "13px" }}>
                  <span>This commitment is {selected.status}.</span>
                </div>
              )}
            </>
          )}
        </div>
      </section>

      <footer>PROMISE · Follow-through intelligence</footer>
    </main>
  );
}
