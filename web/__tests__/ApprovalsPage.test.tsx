/**
 * Integration-style tests for the ApprovalsPage component.
 *
 * The API module and next/navigation are mocked so we can drive the page
 * through different async states without a real backend.
 *
 * Covered states:
 *  - loading spinner while data is fetching
 *  - empty state when no approvals are pending
 *  - backend-down: page-level error notice
 *  - approval card rendered with action + commitment context
 *  - approve flow: loading → success → data refresh (card disappears)
 *  - reject flow: loading → success → data refresh
 *  - conflict (409): per-card conflict notice + data refresh
 *  - error: per-card error notice, buttons re-enabled
 *  - duplicate click prevention: second click while loading is a no-op
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import ApprovalsPage from "../app/approvals/page";

// ---- mock next/navigation (Nav uses usePathname) ----------------------------
vi.mock("next/navigation", () => ({ usePathname: () => "/approvals" }));
vi.mock("next/link", () => ({
  default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));

// ---- mock the api module ----------------------------------------------------
vi.mock("../lib/api", () => ({
  listPendingApprovals: vi.fn(),
  listActions: vi.fn(),
  getCommitments: vi.fn(),
  decideApproval: vi.fn(),
  executeAction: vi.fn(),
}));

import * as api from "../lib/api";
const mockListPending = api.listPendingApprovals as ReturnType<typeof vi.fn>;
const mockListActions = api.listActions as ReturnType<typeof vi.fn>;
const mockGetCommitments = api.getCommitments as ReturnType<typeof vi.fn>;
const mockDecide = api.decideApproval as ReturnType<typeof vi.fn>;
const mockExecute = api.executeAction as ReturnType<typeof vi.fn>;

// ---- mock auth (api.ts imports from ./auth) ---------------------------------
vi.mock("../lib/auth", () => ({ authHeaders: () => ({}) }));

// ---- test fixtures ----------------------------------------------------------

const commitment = {
  id: "com_1",
  title: "Send Andi the revised proposal",
  description: "I'll send Andi the revised proposal tomorrow morning.",
  due_at: null,
  status: "open",
  priority: "medium",
  confidence: 0.97,
};

const action = {
  id: "act_1",
  type: "send_message",
  status: "waiting_for_approval",
  commitment_id: "com_1",
  payload: { recipient: "andi@example.com", subject: "Revised proposal" },
};

const approval = {
  id: "apr_1",
  action_id: "act_1",
  status: "pending",
  requested_at: "2026-10-20T04:00:00Z",
  decided_by: null,
};

function setupDefaultMocks() {
  mockListPending.mockResolvedValue([approval]);
  mockListActions.mockResolvedValue([action]);
  mockGetCommitments.mockResolvedValue([commitment]);
  mockDecide.mockResolvedValue({ ...approval, status: "approved" });
  mockExecute.mockResolvedValue({ action: { ...action, status: "executed" }, commitment });
}

// After a decision the approval is no longer pending — helper kept for reference
// but not used in the test suite directly (each test sets up its own mock calls).

// ---- tests ------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ApprovalsPage – loading state", () => {
  it("shows loading indicator while data is fetching", async () => {
    // Never resolve so loading stays true
    mockListPending.mockReturnValue(new Promise(() => {}));
    mockListActions.mockReturnValue(new Promise(() => {}));
    mockGetCommitments.mockReturnValue(new Promise(() => {}));

    await act(async () => { render(<ApprovalsPage />); });
    expect(screen.getByTestId("loading-indicator")).toBeInTheDocument();
  });
});

describe("ApprovalsPage – empty state", () => {
  it("shows 'Nothing waiting on you' when no approvals pending", async () => {
    mockListPending.mockResolvedValue([]);
    mockListActions.mockResolvedValue([]);
    mockGetCommitments.mockResolvedValue([]);

    await act(async () => { render(<ApprovalsPage />); });
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
  });
});

describe("ApprovalsPage – backend error", () => {
  it("shows page-level error notice when backend is unreachable", async () => {
    mockListPending.mockRejectedValue(new Error("Failed to fetch"));
    mockListActions.mockRejectedValue(new Error("Failed to fetch"));
    mockGetCommitments.mockRejectedValue(new Error("Failed to fetch"));

    await act(async () => { render(<ApprovalsPage />); });
    expect(screen.getByTestId("page-notice")).toHaveTextContent("Could not reach the backend");
  });
});

describe("ApprovalsPage – card rendered", () => {
  it("renders an approval card with action label and commitment context", async () => {
    setupDefaultMocks();
    await act(async () => { render(<ApprovalsPage />); });
    expect(screen.getByTestId("approval-card")).toBeInTheDocument();
    expect(screen.getByTestId("action-label")).toHaveTextContent("send message");
    expect(screen.getByText(/Send Andi the revised proposal/)).toBeInTheDocument();
  });
});

describe("ApprovalsPage – approve flow", () => {
  it("shows 'Approving…' while in flight", async () => {
    setupDefaultMocks();
    // Make decideApproval block so we can observe loading state
    let resolveDecide!: () => void;
    mockDecide.mockReturnValue(new Promise((res) => { resolveDecide = () => res({ ...approval, status: "approved" }); }));

    await act(async () => { render(<ApprovalsPage />); });
    fireEvent.click(screen.getByTestId("btn-approve"));

    await waitFor(() => expect(screen.getByTestId("btn-approve")).toHaveTextContent("Approving…"));
    expect(screen.getByTestId("btn-approve")).toBeDisabled();
    expect(screen.getByTestId("btn-reject")).toBeDisabled();

    // Resolve and let the rest complete
    await act(async () => { resolveDecide(); });
  });

  it("shows success notice after approve+execute", async () => {
    setupDefaultMocks();
    // Keep the approval in pending even after the refresh so the card (and its
    // success state) stays visible long enough to assert on it.
    mockListPending.mockResolvedValue([approval]);

    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-approve")); });

    await waitFor(() =>
      expect(screen.getByTestId("card-notice-success")).toHaveTextContent("Approved and executed.")
    );
  });

  it("calls decideApproval then executeAction in order", async () => {
    setupDefaultMocks();
    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-approve")); });

    await waitFor(() => expect(mockDecide).toHaveBeenCalledWith(approval.id, "approved"));
    expect(mockExecute).toHaveBeenCalledWith(action.id);
  });

  it("refreshes pending list after approve", async () => {
    setupDefaultMocks();
    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-approve")); });

    // listPendingApprovals called once on mount + once on refresh
    await waitFor(() => expect(mockListPending).toHaveBeenCalledTimes(2));
  });
});

describe("ApprovalsPage – reject flow", () => {
  it("shows 'Rejecting…' while in flight", async () => {
    setupDefaultMocks();
    let resolveDecide!: () => void;
    mockDecide.mockReturnValue(new Promise((res) => { resolveDecide = () => res({ ...approval, status: "rejected" }); }));

    await act(async () => { render(<ApprovalsPage />); });
    fireEvent.click(screen.getByTestId("btn-reject"));

    await waitFor(() => expect(screen.getByTestId("btn-reject")).toHaveTextContent("Rejecting…"));

    await act(async () => { resolveDecide(); });
  });

  it("shows success notice after reject", async () => {
    setupDefaultMocks();
    mockDecide.mockResolvedValue({ ...approval, status: "rejected" });

    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-reject")); });

    await waitFor(() =>
      expect(screen.getByTestId("card-notice-success")).toHaveTextContent("Rejected.")
    );
  });

  it("refreshes pending list after reject", async () => {
    setupDefaultMocks();
    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-reject")); });

    await waitFor(() => expect(mockListPending).toHaveBeenCalledTimes(2));
  });
});

describe("ApprovalsPage – conflict (HTTP 409)", () => {
  it("shows conflict notice when decideApproval returns a 409 error", async () => {
    setupDefaultMocks();
    mockDecide.mockRejectedValue(new Error("Request failed (409)"));

    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-approve")); });

    await waitFor(() =>
      expect(screen.getByTestId("card-notice-conflict")).toHaveTextContent("Already decided")
    );
  });

  it("still refreshes data after a 409", async () => {
    setupDefaultMocks();
    mockDecide.mockRejectedValue(new Error("Request failed (409)"));

    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-approve")); });

    await waitFor(() => expect(mockListPending).toHaveBeenCalledTimes(2));
  });
});

describe("ApprovalsPage – generic error", () => {
  it("shows error notice for non-conflict failures", async () => {
    setupDefaultMocks();
    mockDecide.mockRejectedValue(new Error("Gmail integration error: token expired"));

    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-approve")); });

    await waitFor(() =>
      expect(screen.getByTestId("card-notice-error")).toHaveTextContent("Gmail integration error: token expired")
    );
  });

  it("re-enables buttons after an error so the user can retry", async () => {
    setupDefaultMocks();
    mockDecide.mockRejectedValue(new Error("Something broke"));

    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-approve")); });

    await waitFor(() => expect(screen.getByTestId("card-notice-error")).toBeInTheDocument());
    expect(screen.getByTestId("btn-approve")).not.toBeDisabled();
    expect(screen.getByTestId("btn-reject")).not.toBeDisabled();
  });

  it("still refreshes data after a generic error", async () => {
    setupDefaultMocks();
    mockDecide.mockRejectedValue(new Error("Something broke"));

    await act(async () => { render(<ApprovalsPage />); });
    await act(async () => { fireEvent.click(screen.getByTestId("btn-approve")); });

    await waitFor(() => expect(mockListPending).toHaveBeenCalledTimes(2));
  });
});

describe("ApprovalsPage – duplicate click prevention", () => {
  it("ignores second click while approve is in flight", async () => {
    setupDefaultMocks();
    let resolveDecide!: (v: unknown) => void;
    mockDecide.mockReturnValue(new Promise((res) => { resolveDecide = res; }));

    await act(async () => { render(<ApprovalsPage />); });
    fireEvent.click(screen.getByTestId("btn-approve")); // first click

    await waitFor(() => expect(screen.getByTestId("btn-approve")).toBeDisabled());

    // Button is disabled – a second click should be rejected by the browser
    fireEvent.click(screen.getByTestId("btn-approve")); // attempt second click
    expect(mockDecide).toHaveBeenCalledTimes(1); // still only one call

    await act(async () => { resolveDecide({ ...approval, status: "approved" }); });
  });
});
