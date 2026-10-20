"use client";

import { formatDateTime, formatDuration } from "../lib/format";
import type { AgentRun, AgentStep, Commitment } from "../lib/api";

export interface AgentRunCardProps {
  run: AgentRun;
  commitment?: Commitment;
  isExpanded: boolean;
  isLoadingSteps: boolean;
  stepsError?: string | null;
  steps: AgentStep[];
  onToggle: (run: AgentRun) => void;
}

export default function AgentRunCard({
  run,
  commitment,
  isExpanded,
  isLoadingSteps,
  stepsError,
  steps,
  onToggle,
}: AgentRunCardProps) {
  const isFailed = run.status === "failed" || !!run.error;

  return (
    <div
      className={`row-card stacked ${isFailed ? "action-card status-failed" : ""}`}
      data-testid="agent-run-card"
      data-run-id={run.id}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 16,
          cursor: "pointer",
          width: "100%",
        }}
        onClick={() => onToggle(run)}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle(run);
          }
        }}
        data-testid="agent-run-toggle"
      >
        <div className="meta">
          <strong data-testid="run-trigger">{run.trigger}</strong>
          <small>
            {commitment ? commitment.title : "No linked commitment"}
            {" · Started "}
            {formatDateTime(run.started_at)}
            {" · "}
            {formatDuration(run.started_at, run.ended_at)}
          </small>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className={`pill ${run.status}`} data-testid="run-status">
            {run.status.replace(/_/g, " ")}
          </span>
          <button
            type="button"
            className="card-toggle-btn"
            aria-expanded={isExpanded}
            data-testid="toggle-steps-btn"
            onClick={(e) => {
              e.stopPropagation();
              onToggle(run);
            }}
          >
            {isExpanded ? "Hide steps ▲" : "View steps ▼"}
          </button>
        </div>
      </div>

      {isFailed && run.error && (
        <div className="card-notice card-notice--error" data-testid="run-error" role="alert">
          Run failed: {run.error}
        </div>
      )}

      {run.status === "waiting_for_approval" && (
        <div className="card-notice card-notice--conflict" data-testid="run-waiting-notice">
          Waiting for user approval before proceeding with external side effects
        </div>
      )}

      {isExpanded && (
        <div className="steps-detail" data-testid="steps-container">
          {isLoadingSteps && (
            <div className="empty" data-testid="loading-steps" style={{ padding: "16px 0" }}>
              Loading run steps…
            </div>
          )}

          {stepsError && (
            <div className="card-notice card-notice--error" role="alert">
              {stepsError}
            </div>
          )}

          {!isLoadingSteps && !stepsError && steps.length === 0 && (
            <div className="empty" data-testid="empty-steps" style={{ padding: "16px 0" }}>
              No steps recorded for this run.
            </div>
          )}

          {!isLoadingSteps &&
            steps.map((s) => (
              <div className="step-row" key={s.id} data-testid="agent-step-row">
                <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <strong style={{ fontSize: 13, textTransform: "capitalize" }}>{s.name}</strong>
                    {s.tool_calls && s.tool_calls.length > 0 && (
                      <span className="tool-tag" title="Tool calls">
                        {s.tool_calls.join(", ")}
                      </span>
                    )}
                  </div>
                  {s.output_summary && (
                    <span style={{ color: "var(--muted)", fontSize: 12 }}>{s.output_summary}</span>
                  )}
                  {s.error && (
                    <span style={{ color: "var(--red)", fontSize: 12, fontWeight: 600 }}>
                      Error: {s.error}
                    </span>
                  )}
                </div>
                <span className={`pill ${s.status}`}>{s.status}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
