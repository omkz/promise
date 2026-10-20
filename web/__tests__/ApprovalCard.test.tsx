/**
 * Unit tests for the ApprovalCard component.
 *
 * Covers the important UI states requested:
 *  - idle: shows action label, commitment context, payload, approve/reject buttons
 *  - loading approve: "Approving…" button text + disabled
 *  - loading reject: "Rejecting…" button text + disabled
 *  - success: green success banner
 *  - conflict (HTTP 409): amber conflict banner
 *  - error: red error banner
 *  - no action / no commitment: graceful fallback text
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ApprovalCard, { type DecisionState } from "../components/ApprovalCard";
import type { Approval, Action, Commitment } from "../lib/api";

// ---- fixtures ---------------------------------------------------------------

const approval: Approval = {
  id: "apr_1",
  action_id: "act_1",
  status: "pending",
  requested_at: "2026-10-20T04:00:00Z",
  decided_by: null,
};

const action: Action = {
  id: "act_1",
  type: "send_message",
  status: "waiting_for_approval",
  commitment_id: "com_1",
  payload: { recipient: "andi@example.com", subject: "Revised proposal" },
};

const commitment: Commitment = {
  id: "com_1",
  title: "Send Andi the revised proposal",
  description: "I'll send Andi the revised proposal tomorrow morning.",
  due_at: null,
  status: "open",
  priority: "medium",
  confidence: 0.97,
};

// ---- helper -----------------------------------------------------------------

// ---- helper -----------------------------------------------------------------

const NO_ACTION = Symbol("no-action");
const NO_COMMITMENT = Symbol("no-commitment");

function renderCard({
  state = { type: "idle" } as DecisionState,
  onApprove = vi.fn(),
  onReject = vi.fn(),
  act = NO_ACTION as unknown,
  com = NO_COMMITMENT as unknown,
} = {}) {
  const resolvedAction: Action | undefined = act === NO_ACTION ? action : (act as Action | undefined);
  const resolvedCommitment: Commitment | undefined = com === NO_COMMITMENT ? commitment : (com as Commitment | undefined);
  return render(
    <ApprovalCard
      approval={approval}
      action={resolvedAction}
      commitment={resolvedCommitment}
      state={state}
      onApprove={onApprove}
      onReject={onReject}
    />
  );
}

// ---- tests ------------------------------------------------------------------

describe("ApprovalCard – idle state", () => {
  it("renders the human-readable action label", () => {
    renderCard();
    expect(screen.getByTestId("action-label")).toHaveTextContent("send message");
  });

  it("renders the commitment context quote", () => {
    renderCard();
    expect(screen.getByText(/send Andi the revised proposal tomorrow morning/i)).toBeInTheDocument();
  });

  it("renders payload key-value rows", () => {
    renderCard();
    expect(screen.getByText("Recipient")).toBeInTheDocument();
    expect(screen.getByText("andi@example.com")).toBeInTheDocument();
    expect(screen.getByText("Subject")).toBeInTheDocument();
    expect(screen.getByText("Revised proposal")).toBeInTheDocument();
  });

  it("renders approve and reject buttons", () => {
    renderCard();
    expect(screen.getByTestId("btn-approve")).toBeInTheDocument();
    expect(screen.getByTestId("btn-reject")).toBeInTheDocument();
  });

  it("approve button calls onApprove with the approval", () => {
    const onApprove = vi.fn();
    renderCard({ onApprove });
    fireEvent.click(screen.getByTestId("btn-approve"));
    expect(onApprove).toHaveBeenCalledWith(approval);
  });

  it("reject button calls onReject with the approval", () => {
    const onReject = vi.fn();
    renderCard({ onReject });
    fireEvent.click(screen.getByTestId("btn-reject"));
    expect(onReject).toHaveBeenCalledWith(approval);
  });

  it("shows the pending status pill", () => {
    renderCard();
    expect(screen.getByText("pending")).toBeInTheDocument();
  });
});

describe("ApprovalCard – loading: approve", () => {
  const state: DecisionState = { type: "loading", decision: "approve" };

  it("disables both buttons", () => {
    renderCard({ state });
    expect(screen.getByTestId("btn-approve")).toBeDisabled();
    expect(screen.getByTestId("btn-reject")).toBeDisabled();
  });

  it("shows 'Approving…' text on the approve button", () => {
    renderCard({ state });
    expect(screen.getByTestId("btn-approve")).toHaveTextContent("Approving…");
  });

  it("reject button still shows its normal text", () => {
    renderCard({ state });
    expect(screen.getByTestId("btn-reject")).toHaveTextContent("Reject");
  });

  it("does not fire onApprove when approve button is disabled", () => {
    const onApprove = vi.fn();
    renderCard({ state, onApprove });
    fireEvent.click(screen.getByTestId("btn-approve"));
    expect(onApprove).not.toHaveBeenCalled();
  });
});

describe("ApprovalCard – loading: reject", () => {
  const state: DecisionState = { type: "loading", decision: "reject" };

  it("shows 'Rejecting…' text on the reject button", () => {
    renderCard({ state });
    expect(screen.getByTestId("btn-reject")).toHaveTextContent("Rejecting…");
  });

  it("approve button still shows its normal text", () => {
    renderCard({ state });
    expect(screen.getByTestId("btn-approve")).toHaveTextContent("Approve & execute");
  });
});

describe("ApprovalCard – success state", () => {
  const state: DecisionState = { type: "success", message: "Approved and executed." };

  it("renders the success notice", () => {
    renderCard({ state });
    expect(screen.getByTestId("card-notice-success")).toHaveTextContent("Approved and executed.");
  });

  it("does not render conflict or error notices", () => {
    renderCard({ state });
    expect(screen.queryByTestId("card-notice-conflict")).not.toBeInTheDocument();
    expect(screen.queryByTestId("card-notice-error")).not.toBeInTheDocument();
  });
});

describe("ApprovalCard – conflict state (HTTP 409)", () => {
  const state: DecisionState = { type: "conflict", message: "Already decided — refreshing…" };

  it("renders the conflict notice with amber styling class", () => {
    renderCard({ state });
    const notice = screen.getByTestId("card-notice-conflict");
    expect(notice).toHaveTextContent("Already decided — refreshing…");
    expect(notice).toHaveClass("card-notice--conflict");
  });

  it("does not render success or error notices", () => {
    renderCard({ state });
    expect(screen.queryByTestId("card-notice-success")).not.toBeInTheDocument();
    expect(screen.queryByTestId("card-notice-error")).not.toBeInTheDocument();
  });
});

describe("ApprovalCard – error state", () => {
  const state: DecisionState = { type: "error", message: "Gmail integration error: permission denied" };

  it("renders the error notice with error styling class", () => {
    renderCard({ state });
    const notice = screen.getByTestId("card-notice-error");
    expect(notice).toHaveTextContent("Gmail integration error: permission denied");
    expect(notice).toHaveClass("card-notice--error");
  });

  it("buttons are not disabled after an error (user can retry)", () => {
    renderCard({ state });
    expect(screen.getByTestId("btn-approve")).not.toBeDisabled();
    expect(screen.getByTestId("btn-reject")).not.toBeDisabled();
  });
});

describe("ApprovalCard – missing action / commitment", () => {
  function renderNoAction() {
    return render(
      <ApprovalCard
        approval={approval}
        action={undefined}
        commitment={undefined}
        state={{ type: "idle" }}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );
  }

  function renderNoCommitment() {
    return render(
      <ApprovalCard
        approval={approval}
        action={action}
        commitment={undefined}
        state={{ type: "idle" }}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );
  }

  it("falls back to action_id as label when action is undefined", () => {
    renderNoAction();
    expect(screen.getByTestId("action-label")).toHaveTextContent(approval.action_id);
  });

  it("shows 'Unknown commitment' in subtitle when commitment is undefined", () => {
    renderNoCommitment();
    expect(screen.getByText(/Unknown commitment/i)).toBeInTheDocument();
  });

  it("shows no payload rows when action is undefined", () => {
    renderNoAction();
    expect(screen.queryByText("Recipient")).not.toBeInTheDocument();
  });

  it("shows no commitment description quote when commitment is undefined", () => {
    // The quote block is only rendered when commitment?.description is truthy.
    renderNoCommitment();
    expect(screen.queryByText(/tomorrow morning/i)).not.toBeInTheDocument();
  });
});

describe("ApprovalCard – array payload values", () => {
  it("joins array attendees with comma separator", () => {
    const calAction: Action = {
      ...action,
      type: "create_calendar_event",
      payload: { title: "Team sync", attendees: ["a@x.com", "b@x.com"] },
    };
    renderCard({ act: calAction });
    expect(screen.getByText("a@x.com, b@x.com")).toBeInTheDocument();
  });
});
