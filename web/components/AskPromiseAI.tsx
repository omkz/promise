"use client";

import Link from "next/link";
import { useState } from "react";
import { invokeStrandsAgent, createCommitment, type StrandsInvokeResult } from "../lib/api";

type AskPromiseAIProps = {
  onCommitmentCreated?: () => void | Promise<void>;
};

export default function AskPromiseAI({ onCommitmentCreated }: AskPromiseAIProps) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [result, setResult] = useState<StrandsInvokeResult | null>(null);

  async function handleFollowThrough(e?: React.FormEvent) {
    if (e) e.preventDefault();
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt || busy) return;

    setBusy(true);
    setError(null);
    setNotice(null);

    try {
      const res = await invokeStrandsAgent(cleanPrompt);
      setResult(res);
      setPrompt("");
      if (onCommitmentCreated) {
        await onCommitmentCreated();
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "AI follow-through failed";
      // Graceful fallback to deterministic capture if Strands agent is not enabled
      if (msg.includes("Strands agent is disabled")) {
        try {
          const detRes = await createCommitment(cleanPrompt);
          if (detRes.persisted) {
            setNotice("Commitment remembered. (AI agent is currently disabled; enable STRANDS_ENABLED=true for automated follow-through planning.)");
            setPrompt("");
            if (onCommitmentCreated) await onCommitmentCreated();
          } else {
            setError(detRes.reason ?? "Could not detect a commitment.");
          }
        } catch (detErr) {
          setError(detErr instanceof Error ? detErr.message : msg);
        }
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleQuickSave() {
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt || busy) return;

    setBusy(true);
    setError(null);
    setNotice(null);
    setResult(null);

    try {
      const res = await createCommitment(cleanPrompt);
      if (res.persisted) {
        setNotice("Commitment remembered.");
        setPrompt("");
        if (onCommitmentCreated) {
          await onCommitmentCreated();
        }
      } else if (res.needs_confirmation) {
        setNotice(res.message ?? "Needs confirmation — try rephrasing.");
      } else {
        setError(res.reason ?? "That didn't read as a commitment.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to record commitment");
    } finally {
      setBusy(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleFollowThrough();
    }
  }

  return (
    <section className="primary-capture" aria-label="Capture commitment or ask PROMISE">
      <div className="capture-container">
        <form onSubmit={handleFollowThrough}>
          <label htmlFor="primary-prompt-input" className="sr-only">
            Capture a commitment or ask PROMISE
          </label>
          <textarea
            id="primary-prompt-input"
            className="capture-textarea"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Capture a commitment or ask PROMISE to follow through… (e.g. 'I promised Andi the revised proposal tomorrow at 10 AM')"
            rows={2}
            disabled={busy}
            aria-label="Commitment or follow-through instruction"
          />

          <div className="capture-toolbar">
            <div className="capture-hint">
              <span className="sparkle-icon">✦</span>
              <span>AI Follow-through</span>
            </div>

            <div className="capture-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={handleQuickSave}
                disabled={busy || !prompt.trim()}
              >
                Quick save
              </button>
              <button
                type="submit"
                className="btn-primary"
                disabled={busy || !prompt.trim()}
              >
                {busy ? "Processing…" : "Follow through"}
              </button>
            </div>
          </div>
        </form>

        {busy && (
          <div className="status-banner status-banner--processing" role="status">
            <div>
              <span className="pulse-dot" />
              <span>Analyzing commitment and preparing follow-through…</span>
            </div>
          </div>
        )}

        {notice && (
          <div className="status-banner status-banner--success" role="status">
            <span>✓ {notice}</span>
            <button type="button" className="banner-dismiss" onClick={() => setNotice(null)} aria-label="Dismiss notice">✕</button>
          </div>
        )}

        {error && (
          <div className="status-banner status-banner--error" role="alert">
            <span>{error}</span>
            <button type="button" className="banner-dismiss" onClick={() => setError(null)} aria-label="Dismiss error">✕</button>
          </div>
        )}

        {result && (
          <div className="ai-feedback-card" data-testid="ai-feedback-card">
            <div className="ai-feedback-header">
              <div className="ai-feedback-title">
                <span className="ai-badge">PROMISE AI</span>
                <span>Follow-through complete</span>
              </div>
              <button
                type="button"
                className="btn-text"
                onClick={() => setResult(null)}
              >
                Dismiss
              </button>
            </div>

            <p className="ai-feedback-text" data-testid="ai-output-text">
              {result.output_text || "Action planned successfully and waiting for approval."}
            </p>

            <div className="approval-safeguard-banner">
              <span>🔒 Actions require your approval before anything is executed.</span>
              <Link href="/approvals" className="safeguard-link">
                Review in Approvals →
              </Link>
            </div>

            {/* Technical Diagnostics (Collapsible for developers/evaluators, separated from normal UI) */}
            <details className="diagnostics-drawer" data-testid="technical-diagnostics">
              <summary className="diagnostics-summary">
                Technical diagnostics ({result.tool_calls?.length || 0} tool call{(result.tool_calls?.length || 0) === 1 ? "" : "s"})
              </summary>
              <div className="diagnostics-body">
                <div className="diagnostics-row">
                  <span className="diagnostics-label">Provider</span>
                  <span className="diagnostics-value" data-testid="diagnostics-provider">{result.model_provider}</span>
                </div>
                <div className="diagnostics-row">
                  <span className="diagnostics-label">Model</span>
                  <span className="diagnostics-value" data-testid="diagnostics-model">{result.model_id}</span>
                </div>
                <div className="diagnostics-row">
                  <span className="diagnostics-label">Stop reason</span>
                  <span className="diagnostics-value" data-testid="diagnostics-stop-reason">{result.stop_reason}</span>
                </div>

                {result.tool_calls && result.tool_calls.length > 0 && (
                  <div className="diagnostics-tools-list">
                    <span className="diagnostics-label">Executed Tool Calls:</span>
                    {result.tool_calls.map((tc, idx) => (
                      <div key={idx} className="diagnostics-tool-item" data-testid={`tool-call-${idx}`}>
                        <div className="diagnostics-tool-name">
                          <code>{tc.name}</code>
                          {tc.toolUseId && <small>{tc.toolUseId}</small>}
                        </div>
                        {tc.input != null && (
                          <pre className="diagnostics-payload">
                            {typeof tc.input === "string" ? tc.input : JSON.stringify(tc.input, null, 2)}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </details>
          </div>
        )}
      </div>
    </section>
  );
}
