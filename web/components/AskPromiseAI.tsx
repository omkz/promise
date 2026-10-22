"use client";

import Link from "next/link";
import { useState } from "react";
import { invokeStrandsAgent, type StrandsInvokeResult } from "../lib/api";

export const DEMO_AI_PROMPT =
  "I promised Andi I will send the revised Q3 proposal tomorrow at 10 AM. Create this commitment and prepare the action to handle it.";

type AskPromiseAIProps = {
  onCommitmentCreated?: () => void | Promise<void>;
};

export default function AskPromiseAI({ onCommitmentCreated }: AskPromiseAIProps) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StrandsInvokeResult | null>(null);

  async function handleSubmit(e?: React.FormEvent) {
    if (e) e.preventDefault();
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt || busy) return;

    setBusy(true);
    setError(null);

    try {
      const res = await invokeStrandsAgent(cleanPrompt);
      setResult(res);
      if (onCommitmentCreated) {
        await onCommitmentCreated();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Strands AI agent invocation failed");
    } finally {
      setBusy(false);
    }
  }

  function handleUseDemoPrompt() {
    setPrompt(DEMO_AI_PROMPT);
    setError(null);
  }

  return (
    <section className="ai-section" aria-label="Ask PROMISE AI" style={{ marginBottom: "28px" }}>
      <div className="panel ai-panel" style={{ minHeight: "auto" }}>
        <div className="panel-head">
          <div>
            <p className="eyebrow">AUTONOMOUS MULTI-TOOL AGENT</p>
            <h2>Ask PROMISE AI</h2>
            <p className="sub" style={{ fontSize: "14px", marginTop: "6px" }}>
              Orchestrate follow-through tasks with Strands Agents (Groq, Gemini, Anthropic, Bedrock).
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="ai-form" style={{ marginTop: "16px" }}>
          <label
            htmlFor="ai-prompt-input"
            style={{ fontSize: "12px", fontWeight: 800, color: "var(--muted)", display: "block", marginBottom: "8px" }}
          >
            Instructions or commitment request
          </label>
          <textarea
            id="ai-prompt-input"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={DEMO_AI_PROMPT}
            rows={3}
            disabled={busy}
            aria-label="AI prompt"
          />
          <div style={{ display: "flex", gap: "10px", marginTop: "10px", flexWrap: "wrap" }}>
            <button type="submit" disabled={busy || !prompt.trim()}>
              {busy ? "Running AI Agent…" : "Ask PROMISE AI"}
            </button>
            <button
              type="button"
              className="ghost"
              onClick={handleUseDemoPrompt}
              disabled={busy}
            >
              Use Groq demo prompt
            </button>
            {(result || error) && (
              <button
                type="button"
                className="ghost"
                onClick={() => {
                  setResult(null);
                  setError(null);
                }}
                disabled={busy}
              >
                Clear output
              </button>
            )}
          </div>
        </form>

        {busy && (
          <div className="card-notice card-notice--conflict" style={{ marginTop: "16px" }} role="status">
            <span>⚡ AI agent is orchestrating tools and retrieving context…</span>
          </div>
        )}

        {error && (
          <div className="notice notice--error" role="alert" style={{ marginTop: "16px" }}>
            <strong>Configuration / Execution Error:</strong> {error}
          </div>
        )}

        {result && (
          <div className="ai-result" style={{ marginTop: "20px", borderTop: "1px solid var(--line)", paddingTop: "16px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
              <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                <span className="pill approved" data-testid="ai-provider">{result.model_provider}</span>
                <span style={{ fontSize: "12px", fontWeight: 700, color: "var(--muted)" }} data-testid="ai-model">{result.model_id}</span>
                <span className="pill" data-testid="ai-stop-reason">{result.stop_reason}</span>
              </div>
              <span style={{ fontSize: "12px", color: "var(--muted)" }}>
                {result.tool_calls.length} tool call{result.tool_calls.length === 1 ? "" : "s"} executed
              </span>
            </div>

            <div className="ai-response" style={{ marginTop: "12px", background: "#fbfbfd", border: "1px solid var(--line)", borderRadius: "14px", padding: "14px" }}>
              <span style={{ color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".12em", fontSize: "10px", fontWeight: 800, display: "block", marginBottom: "6px" }}>
                Agent Response
              </span>
              <p style={{ margin: 0, lineHeight: 1.5, whiteSpace: "pre-wrap" }} data-testid="ai-output-text">
                {result.output_text || "(No response text returned)"}
              </p>
            </div>

            {result.tool_calls.length > 0 && (
              <div style={{ marginTop: "16px" }}>
                <span style={{ color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".12em", fontSize: "10px", fontWeight: 800, display: "block", marginBottom: "8px" }}>
                  Tool Calls Executed ({result.tool_calls.length})
                </span>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  {result.tool_calls.map((tc, idx) => (
                    <div
                      key={idx}
                      style={{ background: "#ffffff", border: "1px solid var(--line)", borderRadius: "12px", padding: "10px 14px" }}
                      data-testid={`tool-call-${idx}`}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span className="tool-tag">{tc.name}</span>
                        {tc.toolUseId && (
                          <small style={{ color: "var(--muted)", fontFamily: "monospace" }}>{tc.toolUseId}</small>
                        )}
                      </div>
                      {tc.input != null && (
                        <pre style={{ margin: "8px 0 0", padding: "8px 10px", background: "#f8f9fc", border: "1px solid var(--line)", borderRadius: "8px", fontSize: "11px", overflowX: "auto" }}>
                          {typeof tc.input === "string" ? tc.input : JSON.stringify(tc.input, null, 2)}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div
              className="card-notice card-notice--conflict"
              style={{ marginTop: "16px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "10px" }}
            >
              <span>🔒 Approval gating: Proposed external actions require explicit human review.</span>
              <Link href="/approvals" style={{ color: "var(--amber)", fontWeight: 800, textDecoration: "underline" }}>
                Review in Approvals →
              </Link>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
