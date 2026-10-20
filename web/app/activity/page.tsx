"use client";

import { useEffect, useState } from "react";
import Nav from "../../components/Nav";
import AgentRunCard from "../../components/AgentRunCard";
import ActionHistoryCard from "../../components/ActionHistoryCard";
import AuditTrailCard from "../../components/AuditTrailCard";
import { sortActionsNewestFirst } from "../../lib/format";
import {
  getAgentRun,
  getCommitments,
  listActions,
  listAgentRuns,
  listAuditEvents,
  type Action,
  type AgentRun,
  type AgentStep,
  type AuditEvent,
  type Commitment,
} from "../../lib/api";

type ViewTab = "all" | "runs" | "actions" | "audit";
type ActionFilter = "all" | "failed" | "decisions" | "executed";

type PageNotice = { kind: "error" | "info"; message: string };

export default function ActivityPage() {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
  const [commitments, setCommitments] = useState<Record<string, Commitment>>({});
  const [events, setEvents] = useState<AuditEvent[]>([]);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [pageNotice, setPageNotice] = useState<PageNotice | null>(null);

  // Agent run expansion & steps cache
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  const [runSteps, setRunSteps] = useState<Record<string, AgentStep[]>>({});
  const [loadingSteps, setLoadingSteps] = useState<Record<string, boolean>>({});
  const [stepsErrors, setStepsErrors] = useState<Record<string, string>>({});

  // View & filter state
  const [activeTab, setActiveTab] = useState<ViewTab>("all");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("all");

  async function loadData(): Promise<void> {
    const [allRuns, allActions, allEvents, allCommitments] = await Promise.all([
      listAgentRuns(),
      listActions(),
      listAuditEvents(),
      getCommitments(),
    ]);

    setRuns(allRuns);
    setActions(sortActionsNewestFirst(allActions));
    setEvents(allEvents);
    setCommitments(Object.fromEntries(allCommitments.map((c) => [c.id, c])));
    setPageNotice(null);
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([listAgentRuns(), listActions(), listAuditEvents(), getCommitments()])
      .then(([allRuns, allActions, allEvents, allCommitments]) => {
        if (cancelled) return;
        setRuns(allRuns);
        setActions(sortActionsNewestFirst(allActions));
        setEvents(allEvents);
        setCommitments(Object.fromEntries(allCommitments.map((c) => [c.id, c])));
      })
      .catch(() => {
        if (!cancelled) {
          setPageNotice({
            kind: "error",
            message: "Could not reach the backend — start it on port 8000.",
          });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await loadData();
    } catch (e) {
      setPageNotice({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to refresh activity data",
      });
    } finally {
      setRefreshing(false);
    }
  }

  async function toggleRun(run: AgentRun) {
    if (openRunId === run.id) {
      setOpenRunId(null);
      return;
    }

    setOpenRunId(run.id);

    // If steps are already loaded for this run, no need to re-fetch
    if (runSteps[run.id]) {
      return;
    }

    setLoadingSteps((prev) => ({ ...prev, [run.id]: true }));
    setStepsErrors((prev) => {
      const next = { ...prev };
      delete next[run.id];
      return next;
    });

    try {
      const detail = await getAgentRun(run.id);
      setRunSteps((prev) => ({ ...prev, [run.id]: detail.steps }));
    } catch (e) {
      setStepsErrors((prev) => ({
        ...prev,
        [run.id]: e instanceof Error ? e.message : "Failed to load run steps",
      }));
    } finally {
      setLoadingSteps((prev) => ({ ...prev, [run.id]: false }));
    }
  }

  // Filter actions for display
  const failedActionsCount = actions.filter((a) => a.status === "failed" || !!a.error).length;
  const decisionActionsCount = actions.filter(
    (a) => a.status === "approved" || a.status === "rejected" || a.status === "waiting_for_approval"
  ).length;
  const executedActionsCount = actions.filter((a) => a.status === "executed").length;

  const filteredActions = actions.filter((a) => {
    if (actionFilter === "failed") return a.status === "failed" || !!a.error;
    if (actionFilter === "decisions") {
      return a.status === "approved" || a.status === "rejected" || a.status === "waiting_for_approval";
    }
    if (actionFilter === "executed") return a.status === "executed";
    return true;
  });

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <div className="brand">PROMISE</div>
          <div className="tagline">Your AI follow-through agent.</div>
        </div>
        <button
          className="ghost"
          style={{ marginTop: 0, padding: "8px 14px", fontSize: 13 }}
          disabled={loading || refreshing}
          onClick={handleRefresh}
          data-testid="refresh-btn"
        >
          {refreshing ? "Refreshing…" : "↻ Refresh"}
        </button>
      </header>

      <Nav />

      <div className="page-head">
        <p className="eyebrow">OBSERVABILITY & AUDIT TRAIL</p>
        <h1>Activity &amp; Audit</h1>
      </div>

      {pageNotice && (
        <div
          className={`notice${pageNotice.kind === "error" ? " notice--error" : ""}`}
          role={pageNotice.kind === "error" ? "alert" : "status"}
          data-testid="page-notice"
        >
          {pageNotice.message}
        </div>
      )}

      {loading ? (
        <div className="empty" data-testid="loading-indicator">
          Loading activity…
        </div>
      ) : (
        <>
          {/* Navigation view tabs */}
          <div className="view-tabs" role="tablist" data-testid="view-tabs">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "all"}
              className={`view-tab ${activeTab === "all" ? "active" : ""}`}
              onClick={() => setActiveTab("all")}
              data-testid="tab-all"
            >
              All Activity
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "runs"}
              className={`view-tab ${activeTab === "runs" ? "active" : ""}`}
              onClick={() => setActiveTab("runs")}
              data-testid="tab-runs"
            >
              Agent Runs <span className="badge-count">{runs.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "actions"}
              className={`view-tab ${activeTab === "actions" ? "active" : ""}`}
              onClick={() => setActiveTab("actions")}
              data-testid="tab-actions"
            >
              Action History <span className="badge-count">{actions.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "audit"}
              className={`view-tab ${activeTab === "audit" ? "active" : ""}`}
              onClick={() => setActiveTab("audit")}
              data-testid="tab-audit"
            >
              Audit Trail <span className="badge-count">{events.length}</span>
            </button>
          </div>

          {/* Section: Agent Runs */}
          {(activeTab === "all" || activeTab === "runs") && (
            <section style={{ marginBottom: 36 }} data-testid="section-agent-runs">
              <div className="section-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <p className="eyebrow">AGENT WORKFLOW RUNS</p>
                  <h2>Recent agent runs</h2>
                </div>
                <span className="count">{runs.length}</span>
              </div>

              <div className="rows" data-testid="agent-runs-list">
                {runs.length === 0 && (
                  <div className="empty" data-testid="empty-runs">
                    No agent runs yet — try &quot;Handle this&quot; on a commitment.
                  </div>
                )}
                {runs.map((r) => (
                  <AgentRunCard
                    key={r.id}
                    run={r}
                    commitment={r.commitment_id ? commitments[r.commitment_id] : undefined}
                    isExpanded={openRunId === r.id}
                    isLoadingSteps={!!loadingSteps[r.id]}
                    stepsError={stepsErrors[r.id]}
                    steps={runSteps[r.id] || []}
                    onToggle={toggleRun}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Section: Action History & Decisions */}
          {(activeTab === "all" || activeTab === "actions") && (
            <section style={{ marginBottom: 36 }} data-testid="section-action-history">
              <div className="section-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <p className="eyebrow">SIDE EFFECTS &amp; APPROVALS</p>
                  <h2>Action history &amp; decisions</h2>
                </div>
                <span className="count">{actions.length}</span>
              </div>

              {/* Filter pills for action history */}
              <div className="filter-pills" data-testid="action-filter-pills">
                <button
                  type="button"
                  className={`filter-pill ${actionFilter === "all" ? "active" : ""}`}
                  onClick={() => setActionFilter("all")}
                  data-testid="filter-all"
                >
                  All Actions <span className="badge-count">{actions.length}</span>
                </button>
                <button
                  type="button"
                  className={`filter-pill ${actionFilter === "failed" ? "active" : ""}`}
                  onClick={() => setActionFilter("failed")}
                  data-testid="filter-failed"
                >
                  Failed {failedActionsCount > 0 && "⚠️"}{" "}
                  <span className="badge-count">{failedActionsCount}</span>
                </button>
                <button
                  type="button"
                  className={`filter-pill ${actionFilter === "decisions" ? "active" : ""}`}
                  onClick={() => setActionFilter("decisions")}
                  data-testid="filter-decisions"
                >
                  Approval Decisions <span className="badge-count">{decisionActionsCount}</span>
                </button>
                <button
                  type="button"
                  className={`filter-pill ${actionFilter === "executed" ? "active" : ""}`}
                  onClick={() => setActionFilter("executed")}
                  data-testid="filter-executed"
                >
                  Executed <span className="badge-count">{executedActionsCount}</span>
                </button>
              </div>

              <div className="rows" data-testid="actions-list">
                {actions.length === 0 && (
                  <div className="empty" data-testid="empty-actions">
                    No actions recorded yet.
                  </div>
                )}
                {actions.length > 0 && filteredActions.length === 0 && (
                  <div className="empty" data-testid="no-matching-actions">
                    No actions match the &quot;{actionFilter}&quot; filter.
                  </div>
                )}
                {filteredActions.map((a) => (
                  <ActionHistoryCard
                    key={a.id}
                    action={a}
                    commitment={a.commitment_id ? commitments[a.commitment_id] : undefined}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Section: Audit Trail */}
          {(activeTab === "all" || activeTab === "audit") && (
            <section style={{ marginBottom: 36 }} data-testid="section-audit-trail">
              <div className="section-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <p className="eyebrow">EVERYTHING THAT HAPPENED</p>
                  <h2>Audit trail</h2>
                </div>
                <span className="count">{events.length}</span>
              </div>

              <div className="rows" data-testid="audit-trail-list">
                {events.length === 0 && (
                  <div className="empty" data-testid="empty-audit">
                    No audit events recorded yet.
                  </div>
                )}
                {events.map((e) => (
                  <AuditTrailCard key={e.id} event={e} />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </main>
  );
}
