import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import AuditTrailCard from "../components/AuditTrailCard";
import type { AuditEvent } from "../lib/api";

const sampleEvent: AuditEvent = {
  id: "aud_1",
  event_type: "action.executed",
  summary: "Action act_fc27c1a executed",
  created_at: "2026-10-20T06:15:22Z",
  actor: "usr_dev_default",
  entity_type: "action",
  entity_id: "act_fc27c1a",
  agent_run_id: "run_101",
  action_id: "act_fc27c1a",
};

describe("AuditTrailCard", () => {
  it("renders event type, actor, and summary", () => {
    render(<AuditTrailCard event={sampleEvent} />);
    expect(screen.getByTestId("event-type")).toHaveTextContent("action.executed");
    expect(screen.getByTestId("event-actor")).toHaveTextContent("usr_dev_default");
    expect(screen.getByText("Action act_fc27c1a executed")).toBeInTheDocument();
    expect(screen.getByText(/Target: action act_fc27c1a/)).toBeInTheDocument();
  });

  it("applies failed styling when event_type is a failure", () => {
    const failedEvent: AuditEvent = {
      ...sampleEvent,
      event_type: "agent_run.failed",
      summary: "LLM extraction error: Rate limited",
    };
    render(<AuditTrailCard event={failedEvent} />);
    const card = screen.getByTestId("audit-trail-card");
    expect(card).toHaveClass("status-failed");
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });
});
