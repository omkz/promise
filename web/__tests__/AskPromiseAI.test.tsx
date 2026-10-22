import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import AskPromiseAI from "../components/AskPromiseAI";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, className, style }: { href: string; children: React.ReactNode; className?: string; style?: React.CSSProperties }) => (
    <a href={href} className={className} style={style}>{children}</a>
  ),
}));

// Mock api
vi.mock("../lib/api", () => ({
  invokeStrandsAgent: vi.fn(),
  createCommitment: vi.fn(),
}));

import * as api from "../lib/api";
const mockInvokeStrandsAgent = api.invokeStrandsAgent as ReturnType<typeof vi.fn>;
const mockCreateCommitment = api.createCommitment as ReturnType<typeof vi.fn>;

describe("AskPromiseAI component (Unified Primary Input)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the primary capture container, textarea, and action buttons without developer demo buttons", () => {
    render(<AskPromiseAI />);

    expect(screen.getByRole("region", { name: "Capture commitment or ask PROMISE" })).toBeInTheDocument();
    expect(screen.getByLabelText("Commitment or follow-through instruction")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Follow through" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Quick save" })).toBeInTheDocument();

    // Verifies developer demo buttons are removed from normal UI
    expect(screen.queryByRole("button", { name: "Use Groq demo prompt" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use demo sentence" })).not.toBeInTheDocument();
  });

  it("disables buttons when input is empty or whitespace", () => {
    render(<AskPromiseAI />);

    const followThroughBtn = screen.getByRole("button", { name: "Follow through" });
    const quickSaveBtn = screen.getByRole("button", { name: "Quick save" });

    expect(followThroughBtn).toBeDisabled();
    expect(quickSaveBtn).toBeDisabled();

    const textarea = screen.getByLabelText("Commitment or follow-through instruction");
    fireEvent.change(textarea, { target: { value: "   " } });
    expect(followThroughBtn).toBeDisabled();
    expect(quickSaveBtn).toBeDisabled();

    fireEvent.change(textarea, { target: { value: "I'll send Andi the proposal tomorrow" } });
    expect(followThroughBtn).not.toBeDisabled();
    expect(quickSaveBtn).not.toBeDisabled();
  });

  it("calls invokeStrandsAgent on Follow through and renders clean output with collapsible diagnostics", async () => {
    const onCommitmentCreated = vi.fn().mockResolvedValue(undefined);
    mockInvokeStrandsAgent.mockResolvedValueOnce({
      prompt: "I promised Andi I will send the revised Q3 proposal tomorrow at 10 AM.",
      output_text: "I have recorded your commitment to Andi and prepared the follow-through action.",
      model_provider: "groq",
      model_id: "openai/gpt-oss-120b",
      stop_reason: "end_turn",
      tool_calls: [
        {
          name: "create_commitment",
          toolUseId: "call_commit_1",
          input: { text: "Send Andi the revised Q3 proposal" },
        },
        {
          name: "handle_commitment",
          toolUseId: "call_handle_1",
          input: { commitment_id: "com_123" },
        },
      ],
    });

    render(<AskPromiseAI onCommitmentCreated={onCommitmentCreated} />);

    const textarea = screen.getByLabelText("Commitment or follow-through instruction");
    fireEvent.change(textarea, { target: { value: "I promised Andi I will send the revised Q3 proposal tomorrow at 10 AM." } });
    fireEvent.click(screen.getByRole("button", { name: "Follow through" }));

    // Busy state appears
    expect(screen.getByRole("status")).toHaveTextContent("Analyzing commitment and preparing follow-through");

    // Wait for resolution
    await waitFor(() => {
      expect(screen.getByTestId("ai-output-text")).toHaveTextContent("I have recorded your commitment to Andi");
    });

    // Clean human-friendly UI: verify approval safeguard reminder with link to /approvals
    expect(screen.getByText(/Actions require your approval before anything is executed/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review in Approvals →" })).toHaveAttribute("href", "/approvals");

    // Verify technical diagnostics drawer exists and is collapsible
    expect(screen.getByTestId("technical-diagnostics")).toBeInTheDocument();
    expect(screen.getByTestId("diagnostics-provider")).toHaveTextContent("groq");
    expect(screen.getByTestId("diagnostics-model")).toHaveTextContent("openai/gpt-oss-120b");
    expect(screen.getByTestId("diagnostics-stop-reason")).toHaveTextContent("end_turn");
    expect(screen.getByTestId("tool-call-0")).toHaveTextContent("create_commitment");
    expect(screen.getByTestId("tool-call-1")).toHaveTextContent("handle_commitment");

    // Verify onCommitmentCreated callback was executed
    expect(onCommitmentCreated).toHaveBeenCalledTimes(1);

    // Verify NO auto-approve or execute buttons are in this panel
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /execute/i })).not.toBeInTheDocument();
  });

  it("calls createCommitment on Quick save and updates status", async () => {
    const onCommitmentCreated = vi.fn().mockResolvedValue(undefined);
    mockCreateCommitment.mockResolvedValueOnce({
      detected: true,
      persisted: true,
      needs_confirmation: false,
      commitment: {
        id: "com_quick",
        title: "Send report to Andi",
        description: "Send report to Andi tomorrow",
        due_at: null,
        status: "open",
        priority: "medium",
        confidence: 0.95,
      },
    });

    render(<AskPromiseAI onCommitmentCreated={onCommitmentCreated} />);

    const textarea = screen.getByLabelText("Commitment or follow-through instruction");
    fireEvent.change(textarea, { target: { value: "Send report to Andi tomorrow" } });
    fireEvent.click(screen.getByRole("button", { name: "Quick save" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Commitment remembered.");
    });

    expect(mockCreateCommitment).toHaveBeenCalledWith("Send report to Andi tomorrow");
    expect(onCommitmentCreated).toHaveBeenCalledTimes(1);
    expect((textarea as HTMLTextAreaElement).value).toBe("");
  });

  it("displays configuration or backend execution error clearly in the UI", async () => {
    mockInvokeStrandsAgent.mockRejectedValueOnce(
      new Error("Strands agent execution failed with provider 'groq': rate limit")
    );

    render(<AskPromiseAI />);

    const textarea = screen.getByLabelText("Commitment or follow-through instruction");
    fireEvent.change(textarea, { target: { value: "Create commitment" } });
    fireEvent.click(screen.getByRole("button", { name: "Follow through" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    expect(screen.getByRole("alert")).toHaveTextContent("Strands agent execution failed with provider 'groq': rate limit");
  });
});
