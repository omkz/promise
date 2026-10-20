import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ActionHistoryCard from "../components/ActionHistoryCard";
import type { Action, Commitment } from "../lib/api";

const sampleAction: Action = {
  id: "act_101",
  type: "send_message",
  status: "executed",
  commitment_id: "com_101",
  agent_run_id: "run_101",
  payload: {
    recipient: "andi@example.com",
    subject: "Revised proposal",
  },
  result: {
    idempotent_replay: false,
    message_id: "msg_999",
  },
  idempotency_key: "send_message:draft_101",
  created_at: "2026-10-20T06:10:00Z",
  executed_at: "2026-10-20T06:15:00Z",
};

const sampleCommitment: Commitment = {
  id: "com_101",
  title: "Send Andi the revised proposal",
  description: "I promised to email the proposal to Andi.",
  due_at: null,
  status: "completed",
  priority: "high",
  confidence: 0.99,
};

describe("ActionHistoryCard", () => {
  it("renders action type, commitment title, and status", () => {
    render(<ActionHistoryCard action={sampleAction} commitment={sampleCommitment} />);
    expect(screen.getByTestId("action-title")).toHaveTextContent("Send Message");
    expect(screen.getByText(/Send Andi the revised proposal/)).toBeInTheDocument();
    expect(screen.getByTestId("action-status")).toHaveTextContent("executed");
  });

  it("renders commitment description quote if available", () => {
    render(<ActionHistoryCard action={sampleAction} commitment={sampleCommitment} />);
    expect(screen.getByText(/I promised to email the proposal to Andi/)).toBeInTheDocument();
  });

  it("identifies failed actions clearly with error notice", () => {
    const failedAction: Action = {
      ...sampleAction,
      status: "failed",
      error: "Gmail token revoked by user",
    };
    render(<ActionHistoryCard action={failedAction} commitment={sampleCommitment} />);
    const card = screen.getByTestId("action-history-card");
    expect(card).toHaveClass("status-failed");
    expect(screen.getByTestId("action-error")).toHaveTextContent(
      "Action execution failed: Gmail token revoked by user"
    );
  });

  it("identifies approved decision clearly", () => {
    const approvedAction: Action = {
      ...sampleAction,
      status: "approved",
    };
    render(<ActionHistoryCard action={approvedAction} commitment={sampleCommitment} />);
    const card = screen.getByTestId("action-history-card");
    expect(card).toHaveClass("status-approved");
    expect(screen.getByTestId("action-decision-approved")).toHaveTextContent(
      "Approval decision: Approved by user"
    );
  });

  it("identifies rejected decision clearly", () => {
    const rejectedAction: Action = {
      ...sampleAction,
      status: "rejected",
    };
    render(<ActionHistoryCard action={rejectedAction} commitment={sampleCommitment} />);
    const card = screen.getByTestId("action-history-card");
    expect(card).toHaveClass("status-rejected");
    expect(screen.getByTestId("action-decision-rejected")).toHaveTextContent(
      "Approval decision: Rejected by user"
    );
  });

  it("identifies actions waiting for approval", () => {
    const waitingAction: Action = {
      ...sampleAction,
      status: "waiting_for_approval",
    };
    render(<ActionHistoryCard action={waitingAction} commitment={sampleCommitment} />);
    const card = screen.getByTestId("action-history-card");
    expect(card).toHaveClass("status-waiting_for_approval");
    expect(screen.getByTestId("action-decision-waiting")).toHaveTextContent(
      "Awaiting user approval before external side effect"
    );
  });

  it("expands details to show payload parameters, result, and idempotency key", () => {
    render(<ActionHistoryCard action={sampleAction} commitment={sampleCommitment} />);

    // Initially details are hidden
    expect(screen.queryByTestId("action-payload-details")).not.toBeInTheDocument();

    // Click toggle button
    fireEvent.click(screen.getByTestId("toggle-action-details"));

    expect(screen.getByTestId("action-payload-details")).toBeInTheDocument();
    expect(screen.getByText("Recipient")).toBeInTheDocument();
    expect(screen.getByText("andi@example.com")).toBeInTheDocument();
    expect(screen.getByText("Subject")).toBeInTheDocument();
    expect(screen.getByText("Revised proposal")).toBeInTheDocument();
    expect(screen.getByText(/Idempotency: send_message:draft_101/)).toBeInTheDocument();
  });
});
