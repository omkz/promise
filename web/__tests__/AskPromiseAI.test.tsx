import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import AskPromiseAI, { DEMO_AI_PROMPT } from "../components/AskPromiseAI";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, className, style }: { href: string; children: React.ReactNode; className?: string; style?: React.CSSProperties }) => (
    <a href={href} className={className} style={style}>{children}</a>
  ),
}));

// Mock api
vi.mock("../lib/api", () => ({
  invokeStrandsAgent: vi.fn(),
}));

import * as api from "../lib/api";
const mockInvokeStrandsAgent = api.invokeStrandsAgent as ReturnType<typeof vi.fn>;

describe("AskPromiseAI component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the panel, input textarea, and buttons", () => {
    render(<AskPromiseAI />);

    expect(screen.getByRole("region", { name: "Ask PROMISE AI" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask PROMISE AI" })).toBeInTheDocument();
    expect(screen.getByLabelText("AI prompt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask PROMISE AI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use Groq demo prompt" })).toBeInTheDocument();
  });

  it("populates the input with the demo prompt when clicking 'Use Groq demo prompt'", () => {
    render(<AskPromiseAI />);

    const textarea = screen.getByLabelText("AI prompt") as HTMLTextAreaElement;
    expect(textarea.value).toBe("");

    const demoBtn = screen.getByRole("button", { name: "Use Groq demo prompt" });
    fireEvent.click(demoBtn);

    expect(textarea.value).toBe(DEMO_AI_PROMPT);
  });

  it("disables submit button when input is empty or whitespace", () => {
    render(<AskPromiseAI />);

    const submitBtn = screen.getByRole("button", { name: "Ask PROMISE AI" });
    expect(submitBtn).toBeDisabled();

    const textarea = screen.getByLabelText("AI prompt");
    fireEvent.change(textarea, { target: { value: "   " } });
    expect(submitBtn).toBeDisabled();

    fireEvent.change(textarea, { target: { value: "Hello agent" } });
    expect(submitBtn).not.toBeDisabled();
  });

  it("calls invokeStrandsAgent, renders response & tool calls, and calls onCommitmentCreated", async () => {
    const onCommitmentCreated = vi.fn().mockResolvedValue(undefined);
    mockInvokeStrandsAgent.mockResolvedValueOnce({
      prompt: DEMO_AI_PROMPT,
      output_text: "I have created the commitment for Andi and planned the follow-through action.",
      model_provider: "groq",
      model_id: "openai/gpt-oss-120b",
      stop_reason: "end_turn",
      tool_calls: [
        {
          name: "create_commitment",
          toolUseId: "call_commit_1",
          input: { text: "I will send Andi the revised Q3 proposal tomorrow at 10 AM." },
        },
        {
          name: "handle_commitment",
          toolUseId: "call_handle_1",
          input: { commitment_id: "com_123" },
        },
      ],
    });

    render(<AskPromiseAI onCommitmentCreated={onCommitmentCreated} />);

    // Click demo button
    fireEvent.click(screen.getByRole("button", { name: "Use Groq demo prompt" }));
    // Submit
    fireEvent.click(screen.getByRole("button", { name: "Ask PROMISE AI" }));

    // Busy state appears
    expect(screen.getByRole("status")).toHaveTextContent("AI agent is orchestrating tools");

    // Wait for resolution
    await waitFor(() => {
      expect(screen.getByTestId("ai-output-text")).toHaveTextContent("I have created the commitment for Andi");
    });

    // Verify metadata
    expect(screen.getByTestId("ai-provider")).toHaveTextContent("groq");
    expect(screen.getByTestId("ai-model")).toHaveTextContent("openai/gpt-oss-120b");
    expect(screen.getByTestId("ai-stop-reason")).toHaveTextContent("end_turn");

    // Verify tool calls rendered
    expect(screen.getByText("Tool Calls Executed (2)")).toBeInTheDocument();
    expect(screen.getByTestId("tool-call-0")).toHaveTextContent("create_commitment");
    expect(screen.getByTestId("tool-call-1")).toHaveTextContent("handle_commitment");

    // Verify onCommitmentCreated callback was called to refresh dashboard
    expect(onCommitmentCreated).toHaveBeenCalledTimes(1);

    // Verify approval gating advisory is displayed with link to /approvals
    expect(screen.getByText(/Approval gating: Proposed external actions require explicit human review/i)).toBeInTheDocument();
    const approvalsLink = screen.getByRole("link", { name: "Review in Approvals →" });
    expect(approvalsLink).toHaveAttribute("href", "/approvals");

    // Verify there are NO auto-approve or execute buttons inside the AI panel
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /execute/i })).not.toBeInTheDocument();
  });

  it("displays configuration or backend execution error clearly in the UI", async () => {
    mockInvokeStrandsAgent.mockRejectedValueOnce(
      new Error("Strands agent is disabled. Set STRANDS_ENABLED=true in the environment to enable.")
    );

    render(<AskPromiseAI />);

    const textarea = screen.getByLabelText("AI prompt");
    fireEvent.change(textarea, { target: { value: "Create commitment" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask PROMISE AI" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Strands agent is disabled. Set STRANDS_ENABLED=true in the environment to enable."
    );
  });

  it("allows clearing output and error with Clear output button", async () => {
    mockInvokeStrandsAgent.mockRejectedValueOnce(new Error("Provider timeout"));

    render(<AskPromiseAI />);

    fireEvent.change(screen.getByLabelText("AI prompt"), { target: { value: "test" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask PROMISE AI" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    const clearBtn = screen.getByRole("button", { name: "Clear output" });
    fireEvent.click(clearBtn);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
