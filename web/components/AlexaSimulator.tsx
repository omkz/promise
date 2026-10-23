"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import {
  PromiseMcpService,
  DEFAULT_MCP_URL,
  McpConnectionState,
  McpToolCallLog,
  McpCommitmentResult,
  McpHandleResult,
} from "../lib/mcpClient";

// Type definitions for Web Speech API
interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: {
    [index: number]: {
      [index: number]: {
        transcript: string;
      };
      isFinal: boolean;
      length: number;
    };
    length: number;
  };
}

interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
}

interface SpeechRecognitionConstructor {
  new (): SpeechRecognitionLike;
}

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

type DialogEntry = {
  id: string;
  role: "user" | "alexa";
  text: string;
  originalText?: string;
  timestamp: string;
  commitmentResult?: McpCommitmentResult;
  handleResult?: McpHandleResult;
  isProcessing?: boolean;
};

export const QUICK_PROMPTS = [
  "I will send Alex the proposal tomorrow.",
  "I will send Andi the proposal tomorrow.",
  "Can you send the proposal to Alex?",
  "Remind me tomorrow to review the draft.",
];

export default function AlexaSimulator() {
  const [mcpUrl, setMcpUrl] = useState<string>(DEFAULT_MCP_URL);
  const [connection, setConnection] = useState<McpConnectionState>({
    status: "disconnected",
    url: DEFAULT_MCP_URL,
    tools: [],
  });
  const [logs, setLogs] = useState<McpToolCallLog[]>([]);
  const [dialogue, setDialogue] = useState<DialogEntry[]>([]);
  const [inputText, setInputText] = useState("");
  const [isListening, setIsListening] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [interimTranscript, setInterimTranscript] = useState("");
  const [speechSupported, setSpeechSupported] = useState(true);
  const [speechError, setSpeechError] = useState<string | null>(null);

  const mcpServiceRef = useRef<PromiseMcpService | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const dialogueEndRef = useRef<HTMLDivElement | null>(null);

  // Initialize MCP Service
  useEffect(() => {
    const service = new PromiseMcpService(mcpUrl, {
      onLogsChanged: (newLogs) => setLogs(newLogs),
      onStateChanged: (newState) => setConnection(newState),
    });
    mcpServiceRef.current = service;

    // Connect to MCP automatically on mount
    service.connect().catch((err) => {
      console.warn("Auto-connecting to MCP server failed:", err);
    });

    return () => {
      service.disconnect();
    };
  }, [mcpUrl]);

  // Scroll to bottom of dialogue
  useEffect(() => {
    if (dialogueEndRef.current && typeof dialogueEndRef.current.scrollIntoView === "function") {
      dialogueEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [dialogue, interimTranscript]);

  // Voice synthesis helper
  const speakAlexaResponse = useCallback(
    (text: string) => {
      if (isMuted || typeof window === "undefined" || !("speechSynthesis" in window)) {
        return;
      }
      try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.0;
        utterance.pitch = 1.05;
        utterance.onstart = () => setIsSpeaking(true);
        utterance.onend = () => setIsSpeaking(false);
        utterance.onerror = () => setIsSpeaking(false);
        window.speechSynthesis.speak(utterance);
      } catch (err) {
        console.warn("Speech synthesis failed:", err);
        setIsSpeaking(false);
      }
    },
    [isMuted]
  );

  // Handle commitment detection via MCP
  const processUtterance = useCallback(
    async (text: string, confirm = false) => {
      const clean = text.trim();
      if (!clean) return;

      const userEntryId = `entry_user_${Date.now()}`;
      const alexaEntryId = `entry_alexa_${Date.now()}`;

      if (!confirm) {
        setDialogue((prev) => [
          ...prev,
          {
            id: userEntryId,
            role: "user",
            text: clean,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
          {
            id: alexaEntryId,
            role: "alexa",
            text: "Processing with PROMISE...",
            originalText: clean,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
            isProcessing: true,
          },
        ]);
      } else {
        setDialogue((prev) =>
          prev.map((entry) =>
            entry.commitmentResult?.needs_confirmation
              ? { ...entry, isProcessing: true, text: "Capturing commitment..." }
              : entry
          )
        );
      }

      setIsProcessing(true);
      setInputText("");
      setInterimTranscript("");

      try {
        const service = mcpServiceRef.current;
        if (!service) throw new Error("MCP client service is not initialized");

        const result = await service.createCommitment(clean, confirm);

        let alexaText = "";
        if (result.persisted && result.commitment) {
          alexaText = `Got it. I've recorded your commitment: "${result.commitment.title}".`;
        } else if (result.needs_confirmation) {
          alexaText =
            result.message ||
            `I'm thinking this might be a commitment: "${result.commitment?.action || clean}". Should I capture it?`;
        } else {
          alexaText = result.reason || "I didn't detect a personal commitment in that statement.";
        }

        setDialogue((prev) =>
          prev.map((entry) => {
            if (entry.id === alexaEntryId || (confirm && entry.role === "alexa" && entry.isProcessing)) {
              return {
                ...entry,
                text: alexaText,
                isProcessing: false,
                commitmentResult: result,
              };
            }
            return entry;
          })
        );

        speakAlexaResponse(alexaText);
      } catch (err: unknown) {
        const errMessage = err instanceof Error ? err.message : String(err);
        const failText = `I ran into an issue connecting to PROMISE via MCP: ${errMessage}`;

        setDialogue((prev) =>
          prev.map((entry) => {
            if (entry.id === alexaEntryId) {
              return {
                ...entry,
                text: failText,
                isProcessing: false,
              };
            }
            return entry;
          })
        );
        speakAlexaResponse("I had trouble reaching the PROMISE service.");
      } finally {
        setIsProcessing(false);
      }
    },
    [speakAlexaResponse]
  );

  // Handle commitment action planning ("Handle this")
  const handleCommitmentAction = async (commitmentId: string, entryId: string) => {
    setIsProcessing(true);
    try {
      const service = mcpServiceRef.current;
      if (!service) throw new Error("MCP client is not initialized");

      const res = await service.handleCommitment(commitmentId);

      setDialogue((prev) =>
        prev.map((entry) => {
          if (entry.id === entryId) {
            return {
              ...entry,
              handleResult: res,
            };
          }
          return entry;
        })
      );

      if (res.action?.status === "waiting_for_approval") {
        const spoken = "I've planned the follow-through action. It is now waiting for your approval.";
        speakAlexaResponse(spoken);
      } else if (res.error) {
        speakAlexaResponse(`Notice: ${res.error}`);
      }
    } catch (err: unknown) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      setDialogue((prev) =>
        prev.map((entry) => {
          if (entry.id === entryId) {
            return {
              ...entry,
              handleResult: { error: errorMsg },
            };
          }
          return entry;
        })
      );
    } finally {
      setIsProcessing(false);
    }
  };

  // Toggle Voice Recognition
  const toggleListening = () => {
    if (isListening) {
      recognitionRef.current?.stop();
      setIsListening(false);
      return;
    }

    if (!speechSupported) {
      setSpeechError("Speech recognition is not supported in this browser. Please use text input.");
      return;
    }

    setSpeechError(null);
    try {
      const SpeechConstructor = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechConstructor) {
        setSpeechSupported(false);
        return;
      }

      const recognition = new SpeechConstructor();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = "en-US";

      recognition.onstart = () => {
        setIsListening(true);
        setSpeechError(null);
      };

      recognition.onresult = (event: SpeechRecognitionEventLike) => {
        let current = "";
        let final = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const item = event.results[i];
          if (item.isFinal) {
            final += item[0].transcript;
          } else {
            current += item[0].transcript;
          }
        }
        if (final) {
          setIsListening(false);
          processUtterance(final);
        } else {
          setInterimTranscript(current);
        }
      };

      recognition.onerror = (e: { error: string }) => {
        setIsListening(false);
        if (e.error === "not-allowed") {
          setSpeechError("Microphone permission denied. Please allow microphone access or use text input.");
        } else {
          setSpeechError(`Speech error: ${e.error}`);
        }
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      recognitionRef.current = recognition;
      recognition.start();
    } catch (err: unknown) {
      setIsListening(false);
      setSpeechError("Could not start microphone. Please use text input.");
      console.warn("Failed to initialize speech recognition:", err);
    }
  };

  const handleReconnect = () => {
    mcpServiceRef.current?.connect().catch((err) => {
      console.error("Manual reconnect failed:", err);
    });
  };

  // Compute ring state
  const ringState = isListening
    ? "listening"
    : isProcessing
    ? "processing"
    : isSpeaking
    ? "speaking"
    : connection.status === "error"
    ? "error"
    : "idle";

  return (
    <section className="alexa-simulator" aria-label="Alexa+ Simulator">
      {/* Header Banner */}
      <div className="alexa-header">
        <div className="alexa-header-title">
          <div className="alexa-logo-badge">
            <span className="alexa-dot" />
            <span>ALEXA+ SIMULATOR</span>
          </div>
          <span className="alexa-channel-badge">Voice Channel Preview</span>
        </div>

        {/* MCP Connection Status */}
        <div className="alexa-mcp-status" data-testid="mcp-status">
          <span className={`status-indicator status-indicator--${connection.status}`} />
          <span className="mcp-status-text">
            MCP:{" "}
            <strong>
              {connection.status === "connected"
                ? `Connected (${connection.tools.length} tools)`
                : connection.status === "connecting"
                ? "Connecting..."
                : connection.status === "error"
                ? "Connection Error"
                : "Disconnected"}
            </strong>
          </span>
          {connection.status === "error" && (
            <button
              type="button"
              onClick={handleReconnect}
              className="btn-link"
              style={{ marginLeft: "8px", fontSize: "12px" }}
            >
              Retry
            </button>
          )}
          <button
            type="button"
            onClick={() => setIsMuted(!isMuted)}
            className="btn-icon-mute"
            title={isMuted ? "Unmute Alexa voice" : "Mute Alexa voice"}
            aria-label={isMuted ? "Unmute Alexa voice" : "Mute Alexa voice"}
          >
            {isMuted ? "🔇 Muted" : "🔊 Audio on"}
          </button>
        </div>
      </div>

      {/* Connection Error Notice */}
      {connection.status === "error" && (
        <div className="card-notice card-notice--conflict" role="alert" style={{ margin: "16px 24px" }}>
          <strong>MCP Endpoint Unreachable:</strong> Failed to connect to{" "}
          <code>{connection.url}</code>.
          <p style={{ margin: "6px 0 0", fontSize: "12px" }}>
            Ensure the PROMISE MCP server is running:{" "}
            <code>uv run uvicorn promise_mcp.server:app --port 8001</code>
          </p>
        </div>
      )}

      {/* Main Interactive Stage */}
      <div className="alexa-stage">
        {/* Animated Alexa Halo/Ring */}
        <div className={`alexa-ring-wrapper alexa-ring--${ringState}`}>
          <div className="alexa-ring-glow" />
          <div className="alexa-ring-circle">
            <button
              type="button"
              onClick={toggleListening}
              className="alexa-mic-button"
              aria-label={isListening ? "Stop listening" : "Start speaking to Alexa+"}
              title={isListening ? "Stop listening" : "Click to talk"}
            >
              {isListening ? (
                <span className="alexa-mic-icon listening">⏹</span>
              ) : (
                <span className="alexa-mic-icon">🎙</span>
              )}
            </button>
          </div>
        </div>

        <div className="alexa-status-caption">
          {isListening
            ? "Alexa+ is listening... Say a commitment"
            : isProcessing
            ? "Talking to PROMISE via Model Context Protocol..."
            : isSpeaking
            ? "Alexa+ is speaking..."
            : "Click the ring or type a commitment below"}
        </div>

        {speechError && (
          <div className="alexa-speech-error" role="alert">
            {speechError}
          </div>
        )}

        {/* Live Speech Interim Transcript */}
        {interimTranscript && (
          <div className="alexa-interim-transcript" aria-live="polite">
            &ldquo;{interimTranscript}&rdquo;
          </div>
        )}
      </div>

      {/* Quick Prompts Bar */}
      <div className="alexa-quick-prompts">
        <span className="quick-prompts-label">Try asking:</span>
        <div className="quick-chips-list">
          {QUICK_PROMPTS.map((prompt, idx) => (
            <button
              key={idx}
              type="button"
              className="quick-chip"
              onClick={() => processUtterance(prompt)}
              disabled={isProcessing || isListening}
            >
              &ldquo;{prompt}&rdquo;
            </button>
          ))}
        </div>
      </div>

      {/* Conversation Dialogue Display */}
      <div className="alexa-dialogue-container">
        {dialogue.length === 0 ? (
          <div className="alexa-empty-dialogue">
            <p>No conversation yet. Speak or type a commitment to start.</p>
            <small>
              Example: &ldquo;I will send Alex the proposal tomorrow.&rdquo;
            </small>
          </div>
        ) : (
          <div className="alexa-dialogue-list">
            {dialogue.map((entry) => (
              <div key={entry.id} className={`alexa-dialogue-row alexa-dialogue--${entry.role}`}>
                <div className="alexa-avatar">
                  {entry.role === "user" ? "👤" : "⚡"}
                </div>
                <div className="alexa-bubble-content">
                  <div className="alexa-bubble-header">
                    <span className="alexa-bubble-author">
                      {entry.role === "user" ? "You" : "Alexa+"}
                    </span>
                    <span className="alexa-bubble-time">{entry.timestamp}</span>
                  </div>

                  <div className="alexa-bubble-text">
                    {entry.isProcessing ? (
                      <span className="alexa-typing-indicator">
                        <span />
                        <span />
                        <span />
                      </span>
                    ) : (
                      entry.text
                    )}
                  </div>

                  {/* Commitment Result Card when detected */}
                  {entry.commitmentResult?.persisted && entry.commitmentResult.commitment && (
                    <div className="alexa-commitment-card" data-testid="commitment-card">
                      <div className="commitment-card-badge">
                        <span className="badge-pill">Source: Alexa+ (MCP)</span>
                        <span className="badge-status">
                          {entry.commitmentResult.commitment.status}
                        </span>
                      </div>
                      <h4 className="commitment-card-title">
                        {entry.commitmentResult.commitment.title}
                      </h4>
                      <div className="commitment-card-meta">
                        {entry.commitmentResult.commitment.due_at && (
                          <div className="meta-item">
                            <span className="meta-label">Due:</span>{" "}
                            {new Date(entry.commitmentResult.commitment.due_at).toLocaleDateString([], {
                              weekday: "short",
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </div>
                        )}
                        {entry.commitmentResult.contact && (
                          <div className="meta-item">
                            <span className="meta-label">Contact:</span>{" "}
                            {entry.commitmentResult.contact.name}
                          </div>
                        )}
                        <div className="meta-item">
                          <span className="meta-label">Confidence:</span>{" "}
                          {Math.round(entry.commitmentResult.commitment.confidence * 100)}%
                        </div>
                      </div>

                      {/* "Handle this" Action Button & Dashboard Link */}
                      {!entry.handleResult && (
                        <div className="commitment-card-actions" style={{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" }}>
                          <button
                            type="button"
                            className="btn-primary"
                            onClick={() =>
                              handleCommitmentAction(
                                entry.commitmentResult!.commitment!.id,
                                entry.id
                              )
                            }
                            disabled={isProcessing}
                          >
                            Handle this with PROMISE
                          </button>
                          <Link
                            href="/"
                            className="btn-secondary"
                            style={{
                              textDecoration: "none",
                              fontSize: "13px",
                              padding: "8px 14px",
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                            }}
                          >
                            View on Dashboard &rarr;
                          </Link>
                        </div>
                      )}

                      {/* Handle Result Display */}
                      {entry.handleResult && (
                        <div className="handle-result-card" data-testid="handle-result">
                          {entry.handleResult.error ? (
                            <div className="card-notice card-notice--conflict">
                              <strong>Follow-Through Notice:</strong> {entry.handleResult.error}
                            </div>
                          ) : (
                            <div>
                              <div className="handle-result-header">
                                <span className="handle-result-badge">
                                  Action: {entry.handleResult.action?.type || "Proposed"}
                                </span>
                                <span className="handle-result-status">
                                  Status: {entry.handleResult.action?.status || "waiting_for_approval"}
                                </span>
                              </div>
                              <p className="handle-result-description">
                                A follow-through action has been planned by the agent. Per PROMISE governance, external actions require explicit human approval before execution.
                              </p>
                              <div className="approval-safeguard-banner">
                                <span>🔒 Waiting for your decision</span>
                                <Link href="/approvals" className="safeguard-link">
                                  Review in Approvals →
                                </Link>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Needs Confirmation Prompt */}
                  {entry.commitmentResult?.needs_confirmation && (
                    <div className="alexa-confirm-box" data-testid="confirm-box">
                      <p>
                        {entry.commitmentResult.message ||
                          "This statement appears to be a commitment, but requires your confirmation to capture."}
                      </p>
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => processUtterance(entry.originalText || entry.text, true)}
                        disabled={isProcessing}
                      >
                        Yes, capture commitment
                      </button>
                    </div>
                  )}

                  {/* Non-detection explanation */}
                  {entry.commitmentResult &&
                    !entry.commitmentResult.detected &&
                    !entry.commitmentResult.needs_confirmation && (
                      <div className="alexa-not-detected-box" data-testid="not-detected-box">
                        <small>
                          {entry.commitmentResult.reason ||
                            "PROMISE only captures first-person commitments made by you."}
                        </small>
                      </div>
                    )}
                </div>
              </div>
            ))}
            <div ref={dialogueEndRef} />
          </div>
        )}
      </div>

      {/* Text-Input Fallback */}
      <form
        className="alexa-input-bar"
        onSubmit={(e) => {
          e.preventDefault();
          processUtterance(inputText);
        }}
      >
        <input
          type="text"
          className="alexa-text-input"
          placeholder="Or type what you said to Alexa+ (e.g. I will send Alex the proposal tomorrow)..."
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          disabled={isProcessing || isListening}
          aria-label="Alexa speech input text fallback"
        />
        <button
          type="submit"
          className="btn-alexa-send"
          disabled={!inputText.trim() || isProcessing || isListening}
        >
          Send to Alexa+
        </button>
      </form>

      {/* Discreet MCP Activity & Protocol Logs */}
      <details className="diagnostics-drawer alexa-mcp-drawer" data-testid="mcp-logs-drawer">
        <summary className="diagnostics-summary">
          Discreet MCP Protocol Logs ({logs.length} events) · Endpoint: <code>{mcpUrl}</code>
        </summary>
        <div className="diagnostics-body">
          <div className="diagnostics-row" style={{ marginBottom: "12px" }}>
            <span className="diagnostics-label">Server Endpoint</span>
            <input
              type="text"
              value={mcpUrl}
              onChange={(e) => setMcpUrl(e.target.value)}
              className="mcp-url-input"
              style={{
                fontSize: "12px",
                padding: "4px 8px",
                borderRadius: "6px",
                border: "1px solid var(--line)",
                width: "280px",
              }}
            />
          </div>

          <div className="diagnostics-row">
            <span className="diagnostics-label">Connection</span>
            <span className="diagnostics-value">{connection.status}</span>
          </div>

          <div className="diagnostics-row">
            <span className="diagnostics-label">Server Version</span>
            <span className="diagnostics-value">{connection.serverVersion || "N/A"}</span>
          </div>

          <div className="diagnostics-row">
            <span className="diagnostics-label">Discovered Tools ({connection.tools.length})</span>
            <span className="diagnostics-value">
              {connection.tools.length > 0 ? connection.tools.join(", ") : "None discovered"}
            </span>
          </div>

          {logs.length > 0 && (
            <div className="diagnostics-tools-list" style={{ marginTop: "12px" }}>
              <span className="diagnostics-label">Protocol Tool Calls (Streamable HTTP):</span>
              {logs.map((log) => (
                <div key={log.id} className="diagnostics-tool-item">
                  <div className="diagnostics-tool-name">
                    <code>tools/call &rarr; {log.tool}</code>
                    <small>
                      {log.durationMs}ms · {new Date(log.timestamp).toLocaleTimeString()}
                    </small>
                  </div>
                  {log.args && (
                    <div style={{ marginTop: "4px" }}>
                      <span style={{ fontSize: "10px", color: "var(--muted)", textTransform: "uppercase" }}>
                        Arguments:
                      </span>
                      <pre className="diagnostics-payload">{JSON.stringify(log.args, null, 2)}</pre>
                    </div>
                  )}
                  {log.result != null && (
                    <div style={{ marginTop: "4px" }}>
                      <span style={{ fontSize: "10px", color: "var(--muted)", textTransform: "uppercase" }}>
                        Result:
                      </span>
                      <pre className="diagnostics-payload">
                        {typeof log.result === "string" ? log.result : JSON.stringify(log.result, null, 2)}
                      </pre>
                    </div>
                  )}
                  {log.error && (
                    <div style={{ marginTop: "4px", color: "var(--crimson)", fontSize: "11px" }}>
                      <strong>Error:</strong> {log.error}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </details>
    </section>
  );
}
