import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import ActivityPage from "../app/activity/page";
import type { Action, AgentRun, AuditEvent, Commitment } from "../lib/api";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  usePathname: () => "/activity",
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

// Mock api methods
vi.mock("../lib/api", () => ({
  listAgentRuns: vi.fn(),
  listActions: vi.fn(),
  listAuditEvents: vi.fn(),
  getCommitments: vi.fn(),
  getAgentRun: vi.fn(),
}));

import * as api from "../lib/api";
const mockListAgentRuns = api.listAgentRuns as ReturnType<typeof vi.fn>;
const mockListActions = api.listActions as ReturnType<typeof vi.fn>;
const mockListAuditEvents = api.listAuditEvents as ReturnType<typeof vi.fn>;
const mockGetCommitments = api.getCommitments as ReturnType<typeof vi.fn>;
const mockGetAgentRun = api.getAgentRun as ReturnType<typeof vi.fn>;

const sampleRuns: AgentRun[] = [
  {
    id: "run_1",
    commitment_id: "com_1",
    trigger: "api.handle",
    status: "completed",
    started_at: "2026-10-20T06:00:00Z",
    ended_at: "2026-10-20T06:00:02Z",
    action_ids: ["act_1"],
    approval_ids: ["apr_1"],
  },
  {
    id: "run_2",
    commitment_id: null,
    trigger: "alexa.voice",
    status: "failed",
    started_at: "2026-10-20T06:05:00Z",
    ended_at: "2026-10-20T06:05:01Z",
    error: "Failed to extract commitment",
  },
];

const sampleActions: Action[] = [
  {
    id: "act_1",
    type: "send_message",
    status: "executed",
    commitment_id: "com_1",
    payload: { recipient: "andi@example.com" },
    created_at: "2026-10-20T06:00:01Z",
  },
  {
    id: "act_2",
    type: "create_calendar_event",
    status: "failed",
    commitment_id: "com_1",
    error: "Google Calendar permission required",
    payload: { title: "Sync with Team" },
    created_at: "2026-10-20T06:05:00Z",
  },
  {
    id: "act_3",
    type: "publish",
    status: "rejected",
    commitment_id: "com_1",
    payload: { target: "slack" },
    created_at: "2026-10-20T06:10:00Z",
  },
  {
    id: "act_4",
    type: "send_message",
    status: "approved",
    commitment_id: "com_1",
    payload: { recipient: "bob@example.com" },
    created_at: "2026-10-20T06:12:00Z",
  },
];

const sampleCommitments: Commitment[] = [
  {
    id: "com_1",
    title: "Send Andi the revised proposal",
    description: "I'll send Andi the revised proposal.",
    due_at: null,
    status: "open",
    priority: "high",
    confidence: 0.95,
  },
];

const sampleEvents: AuditEvent[] = [
  {
    id: "aud_1",
    event_type: "agent_run.started",
    summary: "Agent run started via 'api.handle'",
    created_at: "2026-10-20T06:00:00Z",
    actor: "usr_1",
  },
];

function setupSuccessMocks() {
  mockListAgentRuns.mockResolvedValue(sampleRuns);
  mockListActions.mockResolvedValue(sampleActions);
  mockListAuditEvents.mockResolvedValue(sampleEvents);
  mockGetCommitments.mockResolvedValue(sampleCommitments);
  mockGetAgentRun.mockResolvedValue({
    run: sampleRuns[0],
    steps: [
      {
        id: "step_1",
        name: "retrieval",
        status: "completed",
        output_summary: "Found context",
        started_at: "2026-10-20T06:00:00Z",
      },
    ],
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ActivityPage", () => {
  it("renders loading indicator on mount", async () => {
    mockListAgentRuns.mockReturnValue(new Promise(() => {}));
    mockListActions.mockReturnValue(new Promise(() => {}));
    mockListAuditEvents.mockReturnValue(new Promise(() => {}));
    mockGetCommitments.mockReturnValue(new Promise(() => {}));

    await act(async () => {
      render(<ActivityPage />);
    });

    expect(screen.getByTestId("loading-indicator")).toBeInTheDocument();
  });

  it("displays error notice when initial load fails", async () => {
    mockListAgentRuns.mockRejectedValue(new Error("Network Error"));
    mockListActions.mockRejectedValue(new Error("Network Error"));
    mockListAuditEvents.mockRejectedValue(new Error("Network Error"));
    mockGetCommitments.mockRejectedValue(new Error("Network Error"));

    await act(async () => {
      render(<ActivityPage />);
    });

    expect(screen.getByTestId("page-notice")).toHaveTextContent(
      "Could not reach the backend — start it on port 8000."
    );
  });

  it("renders empty states when there is no data", async () => {
    mockListAgentRuns.mockResolvedValue([]);
    mockListActions.mockResolvedValue([]);
    mockListAuditEvents.mockResolvedValue([]);
    mockGetCommitments.mockResolvedValue([]);

    await act(async () => {
      render(<ActivityPage />);
    });

    expect(screen.getByTestId("empty-runs")).toHaveTextContent("No agent runs yet");
    expect(screen.getByTestId("empty-actions")).toHaveTextContent("No actions recorded yet.");
    expect(screen.getByTestId("empty-audit")).toHaveTextContent("No audit events recorded yet.");
  });

  it("renders agent runs, actions, and audit events", async () => {
    setupSuccessMocks();

    await act(async () => {
      render(<ActivityPage />);
    });

    // Verify sections present in "all" view
    expect(screen.getByTestId("section-agent-runs")).toBeInTheDocument();
    expect(screen.getByTestId("section-action-history")).toBeInTheDocument();
    expect(screen.getByTestId("section-audit-trail")).toBeInTheDocument();

    // Verify runs
    expect(screen.getByText("api.handle")).toBeInTheDocument();
    expect(screen.getByText("alexa.voice")).toBeInTheDocument();

    // Verify actions
    expect(screen.getAllByText("Send Message").length).toBeGreaterThan(0);
    expect(screen.getByText("Create Calendar Event")).toBeInTheDocument();

    // Verify audit
    expect(screen.getByText("agent run.started")).toBeInTheDocument();
  });

  it("switches views when view tabs are clicked", async () => {
    setupSuccessMocks();

    await act(async () => {
      render(<ActivityPage />);
    });

    // Click "Agent Runs" tab
    fireEvent.click(screen.getByTestId("tab-runs"));
    expect(screen.getByTestId("section-agent-runs")).toBeInTheDocument();
    expect(screen.queryByTestId("section-action-history")).not.toBeInTheDocument();
    expect(screen.queryByTestId("section-audit-trail")).not.toBeInTheDocument();

    // Click "Action History" tab
    fireEvent.click(screen.getByTestId("tab-actions"));
    expect(screen.queryByTestId("section-agent-runs")).not.toBeInTheDocument();
    expect(screen.getByTestId("section-action-history")).toBeInTheDocument();
    expect(screen.queryByTestId("section-audit-trail")).not.toBeInTheDocument();

    // Click "Audit Trail" tab
    fireEvent.click(screen.getByTestId("tab-audit"));
    expect(screen.queryByTestId("section-agent-runs")).not.toBeInTheDocument();
    expect(screen.queryByTestId("section-action-history")).not.toBeInTheDocument();
    expect(screen.getByTestId("section-audit-trail")).toBeInTheDocument();

    // Click "All Activity" tab
    fireEvent.click(screen.getByTestId("tab-all"));
    expect(screen.getByTestId("section-agent-runs")).toBeInTheDocument();
    expect(screen.getByTestId("section-action-history")).toBeInTheDocument();
    expect(screen.getByTestId("section-audit-trail")).toBeInTheDocument();
  });

  it("filters action history by Failed, Approval Decisions, and Executed", async () => {
    setupSuccessMocks();

    await act(async () => {
      render(<ActivityPage />);
    });

    // Filter "Failed"
    fireEvent.click(screen.getByTestId("filter-failed"));
    // act_2 is failed
    expect(screen.getByText("Create Calendar Event")).toBeInTheDocument();
    expect(screen.getByText(/Google Calendar permission required/)).toBeInTheDocument();
    expect(screen.queryByText("Publish")).not.toBeInTheDocument();

    // Filter "Approval Decisions" (act_3 rejected, act_4 approved)
    fireEvent.click(screen.getByTestId("filter-decisions"));
    expect(screen.getByText("Publish")).toBeInTheDocument();
    expect(screen.getByText(/Approval decision: Rejected/)).toBeInTheDocument();
    expect(screen.getByText(/Approval decision: Approved/)).toBeInTheDocument();
    expect(screen.queryByText("Create Calendar Event")).not.toBeInTheDocument();

    // Filter "Executed" (act_1 executed)
    fireEvent.click(screen.getByTestId("filter-executed"));
    expect(screen.getByText("Send Message")).toBeInTheDocument();
    expect(screen.queryByText("Publish")).not.toBeInTheDocument();
  });

  it("expands run steps when an agent run card is clicked", async () => {
    setupSuccessMocks();

    await act(async () => {
      render(<ActivityPage />);
    });

    const runCards = screen.getAllByTestId("agent-run-toggle");
    fireEvent.click(runCards[0]);

    await waitFor(() => {
      expect(mockGetAgentRun).toHaveBeenCalledWith("run_1");
    });

    await waitFor(() => {
      expect(screen.getByText("Found context")).toBeInTheDocument();
    });
  });

  it("re-fetches data when Refresh button is clicked", async () => {
    setupSuccessMocks();

    await act(async () => {
      render(<ActivityPage />);
    });

    fireEvent.click(screen.getByTestId("refresh-btn"));

    await waitFor(() => {
      expect(mockListAgentRuns).toHaveBeenCalledTimes(2);
      expect(mockListActions).toHaveBeenCalledTimes(2);
      expect(mockListAuditEvents).toHaveBeenCalledTimes(2);
    });
  });

  it("displays action history sorted newest activity first", async () => {
    const unsortedActions: Action[] = [
      {
        id: "act_oldest",
        type: "publish",
        status: "executed",
        commitment_id: "com_1",
        payload: {},
        created_at: "2026-10-20T01:00:00Z",
      },
      {
        id: "act_newest",
        type: "send_message",
        status: "executed",
        commitment_id: "com_1",
        payload: {},
        created_at: "2026-10-20T01:00:00Z",
        executed_at: "2026-10-20T05:00:00Z",
      },
      {
        id: "act_middle",
        type: "create_calendar_event",
        status: "failed",
        commitment_id: "com_1",
        payload: {},
        created_at: "2026-10-20T01:00:00Z",
        updated_at: "2026-10-20T03:00:00Z",
      },
    ];

    mockListAgentRuns.mockResolvedValue([]);
    mockListActions.mockResolvedValue(unsortedActions);
    mockListAuditEvents.mockResolvedValue([]);
    mockGetCommitments.mockResolvedValue(sampleCommitments);

    await act(async () => {
      render(<ActivityPage />);
    });

    const actionCards = screen.getAllByTestId("action-history-card");
    expect(actionCards).toHaveLength(3);
    expect(actionCards[0]).toHaveAttribute("data-action-id", "act_newest");
    expect(actionCards[1]).toHaveAttribute("data-action-id", "act_middle");
    expect(actionCards[2]).toHaveAttribute("data-action-id", "act_oldest");
  });
});
