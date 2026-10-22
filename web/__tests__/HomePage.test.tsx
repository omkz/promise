import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import Home from "../app/page";

// Mock next/navigation
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("next/link", () => ({
  default: ({ href, children, className, style }: { href: string; children: React.ReactNode; className?: string; style?: React.CSSProperties }) => (
    <a href={href} className={className} style={style}>{children}</a>
  ),
}));

// Mock api
vi.mock("../lib/api", () => ({
  getCommitments: vi.fn(),
  createCommitment: vi.fn(),
  handleCommitment: vi.fn(),
  decideApproval: vi.fn(),
  executeAction: vi.fn(),
  invokeStrandsAgent: vi.fn(),
}));

import * as api from "../lib/api";
const mockGetCommitments = api.getCommitments as ReturnType<typeof vi.fn>;
const mockCreateCommitment = api.createCommitment as ReturnType<typeof vi.fn>;
const mockInvokeStrandsAgent = api.invokeStrandsAgent as ReturnType<typeof vi.fn>;

describe("Home Page with Ask PROMISE AI integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCommitments.mockResolvedValue([
      {
        id: "com_1",
        title: "Existing commitment",
        description: "Existing commitment description",
        due_at: null,
        status: "open",
        priority: "medium",
        confidence: 0.95,
      },
    ]);
  });

  it("renders both the deterministic capture card and the Ask PROMISE AI panel", async () => {
    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText("Existing commitment")).toBeInTheDocument();
    });

    // Deterministic hero card exists
    expect(screen.getByText("Say a commitment")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Detect commitment" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use demo sentence" })).toBeInTheDocument();

    // Ask PROMISE AI panel exists
    expect(screen.getByRole("region", { name: "Ask PROMISE AI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask PROMISE AI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use Groq demo prompt" })).toBeInTheDocument();
  });

  it("refreshes commitments list when Ask PROMISE AI succeeds", async () => {
    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText("Existing commitment")).toBeInTheDocument();
    });
    expect(mockGetCommitments).toHaveBeenCalledTimes(1);

    mockGetCommitments.mockResolvedValueOnce([
      {
        id: "com_1",
        title: "Existing commitment",
        description: "Existing commitment description",
        due_at: null,
        status: "open",
        priority: "medium",
        confidence: 0.95,
      },
      {
        id: "com_2",
        title: "Send Andi the revised Q3 proposal",
        description: "I will send Andi the revised Q3 proposal tomorrow at 10 AM.",
        due_at: "2026-10-23T10:00:00Z",
        status: "waiting_for_approval",
        priority: "high",
        confidence: 0.98,
      },
    ]);

    mockInvokeStrandsAgent.mockResolvedValueOnce({
      prompt: "I promised Andi I will send the revised Q3 proposal tomorrow at 10 AM. Create this commitment and prepare the action to handle it.",
      output_text: "Created commitment com_2 and proposed action.",
      model_provider: "groq",
      model_id: "openai/gpt-oss-120b",
      stop_reason: "end_turn",
      tool_calls: [
        { name: "create_commitment", input: { text: "Send Andi revised Q3 proposal" } },
        { name: "handle_commitment", input: { commitment_id: "com_2" } },
      ],
    });

    // Populate AI demo prompt and submit
    fireEvent.click(screen.getByRole("button", { name: "Use Groq demo prompt" }));
    fireEvent.click(screen.getByRole("button", { name: "Ask PROMISE AI" }));

    // Verify commitments re-fetched and new commitment rendered in list
    await waitFor(() => {
      expect(screen.getByText("Send Andi the revised Q3 proposal")).toBeInTheDocument();
    });
    expect(mockGetCommitments).toHaveBeenCalledTimes(2);
  });

  it("preserves deterministic detection flow", async () => {
    mockCreateCommitment.mockResolvedValueOnce({
      detected: true,
      persisted: true,
      needs_confirmation: false,
      commitment: {
        id: "com_3",
        title: "Deterministic commitment",
        description: "Deterministic text",
        due_at: null,
        status: "open",
        priority: "low",
        confidence: 0.99,
      },
    });

    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText("Existing commitment")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Use demo sentence" }));
    fireEvent.click(screen.getByRole("button", { name: "Detect commitment" }));

    await waitFor(() => {
      expect(screen.getByText("Commitment detected and remembered.")).toBeInTheDocument();
    });
  });
});
