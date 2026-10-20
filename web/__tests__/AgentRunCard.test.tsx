import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import AgentRunCard, { type AgentRunCardProps } from "../components/AgentRunCard";
import type { AgentRun, AgentStep, Commitment } from "../lib/api";

const sampleRun: AgentRun = {
  id: "run_123",
  commitment_id: "com_1",
  trigger: "api.handle",
  status: "completed",
  started_at: "2026-10-20T06:00:00Z",
  ended_at: "2026-10-20T06:00:03Z",
  action_ids: ["act_1"],
  approval_ids: ["apr_1"],
  error: null,
};

const sampleCommitment: Commitment = {
  id: "com_1",
  title: "Send Andi the revised proposal",
  description: "I'll send Andi the proposal tomorrow",
  due_at: null,
  status: "open",
  priority: "medium",
  confidence: 0.95,
};

const sampleSteps: AgentStep[] = [
  {
    id: "step_1",
    name: "retrieval",
    status: "completed",
    output_summary: "Found 2 documents",
    started_at: "2026-10-20T06:00:01Z",
    tool_calls: ["context_retriever.retrieve"],
  },
  {
    id: "step_2",
    name: "execution",
    status: "failed",
    output_summary: null,
    error: "Provider timeout",
    started_at: "2026-10-20T06:00:02Z",
    tool_calls: ["execution.execute_action"],
  },
];

function renderCard(props: Partial<AgentRunCardProps> = {}) {
  const defaultProps: AgentRunCardProps = {
    run: sampleRun,
    commitment: sampleCommitment,
    isExpanded: false,
    isLoadingSteps: false,
    stepsError: null,
    steps: [],
    onToggle: vi.fn(),
    ...props,
  };
  return render(<AgentRunCard {...defaultProps} />);
}

describe("AgentRunCard", () => {
  it("renders trigger, commitment title, and status pill", () => {
    renderCard();
    expect(screen.getByTestId("run-trigger")).toHaveTextContent("api.handle");
    expect(screen.getByText(/Send Andi the revised proposal/)).toBeInTheDocument();
    expect(screen.getByTestId("run-status")).toHaveTextContent("completed");
    expect(screen.getByText(/3\.0s/)).toBeInTheDocument();
  });

  it("handles fallback when no commitment is linked", () => {
    renderCard({ commitment: undefined });
    expect(screen.getByText(/No linked commitment/)).toBeInTheDocument();
  });

  it("displays prominent error notice when run failed", () => {
    const failedRun: AgentRun = {
      ...sampleRun,
      status: "failed",
      error: "Bedrock extraction model rate limited",
    };
    renderCard({ run: failedRun });
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "Run failed: Bedrock extraction model rate limited"
    );
  });

  it("displays waiting notice when run is waiting for approval", () => {
    const waitingRun: AgentRun = {
      ...sampleRun,
      status: "waiting_for_approval",
    };
    renderCard({ run: waitingRun });
    expect(screen.getByTestId("run-waiting-notice")).toBeInTheDocument();
  });

  it("calls onToggle when header or button is clicked", () => {
    const onToggle = vi.fn();
    renderCard({ onToggle });

    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(onToggle).toHaveBeenCalledWith(sampleRun);

    const button = screen.getByTestId("toggle-steps-btn");
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledTimes(2);
  });

  it("shows loading indicator when expanding steps", () => {
    renderCard({ isExpanded: true, isLoadingSteps: true });
    expect(screen.getByTestId("loading-steps")).toHaveTextContent("Loading run steps…");
  });

  it("shows error when step retrieval fails", () => {
    renderCard({ isExpanded: true, stepsError: "Could not fetch steps" });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not fetch steps");
  });

  it("shows empty state when run has no steps", () => {
    renderCard({ isExpanded: true, steps: [] });
    expect(screen.getByTestId("empty-steps")).toHaveTextContent("No steps recorded for this run.");
  });

  it("renders step rows with tool calls and errors when steps are provided", () => {
    renderCard({ isExpanded: true, steps: sampleSteps });
    const stepRows = screen.getAllByTestId("agent-step-row");
    expect(stepRows).toHaveLength(2);
    expect(screen.getByText("retrieval")).toBeInTheDocument();
    expect(screen.getByText("context_retriever.retrieve")).toBeInTheDocument();
    expect(screen.getByText("Found 2 documents")).toBeInTheDocument();

    expect(screen.getByText("execution")).toBeInTheDocument();
    expect(screen.getByText("Error: Provider timeout")).toBeInTheDocument();
  });
});
