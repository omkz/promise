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
  listActions: vi.fn(),
  listPendingApprovals: vi.fn(),
}));

import * as api from "../lib/api";
const mockGetCommitments = api.getCommitments as ReturnType<typeof vi.fn>;
const mockInvokeStrandsAgent = api.invokeStrandsAgent as ReturnType<typeof vi.fn>;
const mockHandleCommitment = api.handleCommitment as ReturnType<typeof vi.fn>;
const mockDecideApproval = api.decideApproval as ReturnType<typeof vi.fn>;
const mockExecuteAction = api.executeAction as ReturnType<typeof vi.fn>;
const mockListActions = api.listActions as ReturnType<typeof vi.fn>;
const mockListPendingApprovals = api.listPendingApprovals as ReturnType<typeof vi.fn>;

describe("Home Page Redesign (Production-Style AI Productivity)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCommitments.mockResolvedValue([
      {
        id: "com_1",
        title: "Send Andi revised proposal",
        description: "I'll send Andi the revised proposal tomorrow morning.",
        due_at: "2026-10-23T09:00:00Z",
        status: "open",
        priority: "high",
        confidence: 0.95,
      },
    ]);
    mockListActions.mockResolvedValue([]);
    mockListPendingApprovals.mockResolvedValue([]);
  });

  it("renders the unified primary natural-language input and no duplicate input card", async () => {
    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText("Send Andi revised proposal")).toBeInTheDocument();
    });

    // Unified primary input is present
    expect(screen.getByRole("region", { name: "Capture commitment or ask PROMISE" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Follow through" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Quick save" })).toBeInTheDocument();

    // Verifies duplicate input panels and developer text are removed
    expect(screen.queryByText("Say a commitment")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Detect commitment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use demo sentence" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use Groq demo prompt" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Dev workspace/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Workspace active/i)).toBeInTheDocument();
  });

  it("refreshes commitments list when AI follow-through succeeds", async () => {
    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText("Send Andi revised proposal")).toBeInTheDocument();
    });
    expect(mockGetCommitments).toHaveBeenCalledTimes(1);

    mockGetCommitments.mockResolvedValueOnce([
      {
        id: "com_1",
        title: "Send Andi revised proposal",
        description: "I'll send Andi the revised proposal tomorrow morning.",
        due_at: "2026-10-23T09:00:00Z",
        status: "open",
        priority: "high",
        confidence: 0.95,
      },
      {
        id: "com_2",
        title: "Prepare Q3 contract",
        description: "I promised Sarah the Q3 contract by Friday.",
        due_at: "2026-10-24T17:00:00Z",
        status: "waiting_for_approval",
        priority: "high",
        confidence: 0.98,
      },
    ]);

    mockInvokeStrandsAgent.mockResolvedValueOnce({
      prompt: "I promised Sarah the Q3 contract by Friday.",
      output_text: "Created commitment and proposed draft email for Sarah.",
      model_provider: "groq",
      model_id: "openai/gpt-oss-120b",
      stop_reason: "end_turn",
      tool_calls: [
        { name: "create_commitment", input: { text: "Prepare Q3 contract" } },
        { name: "handle_commitment", input: { commitment_id: "com_2" } },
      ],
    });

    const textarea = screen.getByLabelText("Commitment or follow-through instruction");
    fireEvent.change(textarea, { target: { value: "I promised Sarah the Q3 contract by Friday." } });
    fireEvent.click(screen.getByRole("button", { name: "Follow through" }));

    await waitFor(() => {
      expect(screen.getByText("Prepare Q3 contract")).toBeInTheDocument();
    });
    expect(mockGetCommitments).toHaveBeenCalledTimes(2);
  });

  it("selects a commitment, plans follow-through with AI, and presents human-friendly approval controls", async () => {
    mockHandleCommitment.mockResolvedValueOnce({
      agent_run: { id: "run_1", status: "completed" },
      commitment: { id: "com_1", title: "Send Andi revised proposal", status: "waiting_for_approval" },
      contact: { id: "con_andi", name: "Andi", email: "andi@example.com" },
      document: null,
      changes: [],
      draft: {
        id: "d_1",
        recipient: "andi@example.com",
        subject: "Revised proposal as promised",
        body: "Hi Andi, here is the revised proposal as promised.",
        status: "draft",
      },
      action: { id: "act_1", type: "send_email", status: "waiting_for_approval", commitment_id: "com_1", payload: {} },
      approval: { id: "appr_1", action_id: "act_1", status: "pending", requested_at: "2026-10-22T10:00:00Z", decided_by: null },
    });

    mockDecideApproval.mockResolvedValueOnce({ id: "appr_1", status: "approved" });
    mockExecuteAction.mockResolvedValueOnce({ action: { id: "act_1", status: "executed" }, commitment: null });

    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText("Send Andi revised proposal")).toBeInTheDocument();
    });

    // Click on the commitment in the list to select it
    fireEvent.click(screen.getByText("Send Andi revised proposal"));

    // Detail view renders metadata and "Plan follow-through with AI" button
    expect(screen.getByRole("button", { name: "Plan follow-through with AI" })).toBeInTheDocument();

    // Click "Plan follow-through with AI"
    fireEvent.click(screen.getByRole("button", { name: "Plan follow-through with AI" }));

    // Verifies human-friendly action presentation
    await waitFor(() => {
      expect(screen.getByText("Proposed Follow-Through Action")).toBeInTheDocument();
      expect(screen.getByText("Revised proposal as promised")).toBeInTheDocument();
      expect(screen.getByText("Hi Andi, here is the revised proposal as promised.")).toBeInTheDocument();
    });

    // Verifies approval safeguard buttons are present
    const approveBtn = screen.getByRole("button", { name: "Approve & send" });
    const rejectBtn = screen.getByRole("button", { name: "Reject" });
    expect(approveBtn).toBeInTheDocument();
    expect(rejectBtn).toBeInTheDocument();

    // Click Approve & send
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(mockDecideApproval).toHaveBeenCalledWith("appr_1", "approved");
      expect(mockExecuteAction).toHaveBeenCalledWith("act_1");
    });
  });
});
