import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import AlexaSimulator, { QUICK_PROMPTS } from "../components/AlexaSimulator";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));

// Mock mcpClient
const mockConnect = vi.fn();
const mockDisconnect = vi.fn();
const mockCreateCommitment = vi.fn();
const mockHandleCommitment = vi.fn();
const mockGetState = vi.fn();
const mockGetLogs = vi.fn();

let stateCallback: ((state: unknown) => void) | undefined;

vi.mock("../lib/mcpClient", () => {
  return {
    DEFAULT_MCP_URL: "http://127.0.0.1:8001/mcp",
    PromiseMcpService: class {
      connect = mockConnect;
      disconnect = mockDisconnect;
      createCommitment = mockCreateCommitment;
      handleCommitment = mockHandleCommitment;
      getState = mockGetState;
      getLogs = mockGetLogs;
      constructor(
        _url: string,
        callbacks?: { onStateChanged?: (s: unknown) => void }
      ) {
        stateCallback = callbacks?.onStateChanged;
      }
    },
  };
});

describe("AlexaSimulator Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetState.mockReturnValue({
      status: "connected",
      url: "http://127.0.0.1:8001/mcp",
      tools: ["create_commitment", "handle_commitment"],
      serverVersion: "0.1.0",
    });
    mockGetLogs.mockReturnValue([]);
    mockConnect.mockImplementation(async () => {
      stateCallback?.({
        status: "connected",
        url: "http://127.0.0.1:8001/mcp",
        tools: ["create_commitment", "handle_commitment"],
        serverVersion: "0.1.0",
      });
      return { status: "connected" };
    });
    mockDisconnect.mockResolvedValue(undefined);
  });

  it("renders the Alexa+ simulator header, status, mic button, and quick prompts", async () => {
    render(<AlexaSimulator />);

    expect(screen.getByRole("region", { name: "Alexa+ Simulator" })).toBeInTheDocument();
    expect(screen.getByText("ALEXA+ SIMULATOR")).toBeInTheDocument();
    expect(screen.getByText("Voice Channel Preview")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start speaking to Alexa+" })).toBeInTheDocument();

    // Quick prompts
    for (const prompt of QUICK_PROMPTS) {
      expect(screen.getByRole("button", { name: `“${prompt}”` })).toBeInTheDocument();
    }
  });

  it("captures commitment successfully via MCP when clicking quick prompt", async () => {
    mockCreateCommitment.mockResolvedValueOnce({
      detected: true,
      persisted: true,
      needs_confirmation: false,
      commitment: {
        id: "com_alex_123",
        title: "Send proposal",
        action: "Send proposal",
        due_at: "2026-10-23T09:00:00+07:00",
        priority: "high",
        status: "open",
        confidence: 0.97,
      },
      contact: {
        id: "con_alex",
        name: "Alex",
        email: null,
      },
    });

    render(<AlexaSimulator />);

    const alexPromptBtn = screen.getByRole("button", { name: `“${QUICK_PROMPTS[0]}”` });
    fireEvent.click(alexPromptBtn);

    await waitFor(() => {
      expect(mockCreateCommitment).toHaveBeenCalledWith("I will send Alex the proposal tomorrow.", false);
    });

    await waitFor(() => {
      expect(screen.getByTestId("commitment-card")).toBeInTheDocument();
      expect(screen.getByText("Send proposal")).toBeInTheDocument();
      expect(screen.getByText("Source: Alexa+ (MCP)")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Handle this with PROMISE" })).toBeInTheDocument();
    });
  });

  it("handles 'Handle this with PROMISE' and displays action waiting for approval", async () => {
    mockCreateCommitment.mockResolvedValueOnce({
      detected: true,
      persisted: true,
      commitment: {
        id: "com_andi_456",
        title: "Send proposal to Andi",
        action: "Send proposal",
        due_at: "2026-10-23T09:00:00+07:00",
        priority: "high",
        status: "open",
        confidence: 0.97,
      },
      contact: {
        id: "con_andi",
        name: "Andi",
        email: "andi@example.com",
      },
    });

    mockHandleCommitment.mockResolvedValueOnce({
      action: {
        id: "act_999",
        type: "send_message",
        status: "waiting_for_approval",
      },
      approval: {
        id: "apr_999",
        status: "pending",
      },
    });

    render(<AlexaSimulator />);

    const andiPromptBtn = screen.getByRole("button", { name: `“${QUICK_PROMPTS[1]}”` });
    fireEvent.click(andiPromptBtn);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Handle this with PROMISE" })).toBeInTheDocument();
    });

    const handleBtn = screen.getByRole("button", { name: "Handle this with PROMISE" });
    fireEvent.click(handleBtn);

    await waitFor(() => {
      expect(mockHandleCommitment).toHaveBeenCalledWith("com_andi_456");
      expect(screen.getByTestId("handle-result")).toBeInTheDocument();
      expect(screen.getByText("Action: send_message")).toBeInTheDocument();
      expect(screen.getByText("Status: waiting_for_approval")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Review in Approvals/i })).toBeInTheDocument();
    });
  });

  it("displays non-detection reason when utterance is not a commitment", async () => {
    mockCreateCommitment.mockResolvedValueOnce({
      detected: false,
      persisted: false,
      reason: "No personal commitment was detected in that statement.",
    });

    render(<AlexaSimulator />);

    const questionBtn = screen.getByRole("button", { name: `“${QUICK_PROMPTS[2]}”` });
    fireEvent.click(questionBtn);

    await waitFor(() => {
      expect(mockCreateCommitment).toHaveBeenCalledWith("Can you send the proposal to Alex?", false);
      const notDetectedBox = screen.getByTestId("not-detected-box");
      expect(notDetectedBox).toBeInTheDocument();
      expect(within(notDetectedBox).getByText("No personal commitment was detected in that statement.")).toBeInTheDocument();
    });
  });

  it("displays confirmation prompt when needs_confirmation is true and allows confirming", async () => {
    mockCreateCommitment
      .mockResolvedValueOnce({
        detected: true,
        persisted: false,
        needs_confirmation: true,
        message: 'I\'m thinking this may be a commitment: "Review the contract". Capture it?',
      })
      .mockResolvedValueOnce({
        detected: true,
        persisted: true,
        commitment: {
          id: "com_rev_777",
          title: "Review the contract",
          action: "Review the contract",
          due_at: null,
          priority: "medium",
          status: "open",
          confidence: 0.65,
        },
      });

    render(<AlexaSimulator />);

    const input = screen.getByLabelText("Alexa speech input text fallback");
    fireEvent.change(input, { target: { value: "Review the contract next week." } });

    const sendBtn = screen.getByRole("button", { name: "Send to Alexa+" });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      const confirmBox = screen.getByTestId("confirm-box");
      expect(confirmBox).toBeInTheDocument();
      expect(within(confirmBox).getByText('I\'m thinking this may be a commitment: "Review the contract". Capture it?')).toBeInTheDocument();
      expect(within(confirmBox).getByRole("button", { name: "Yes, capture commitment" })).toBeInTheDocument();
    });

    const confirmBtn = screen.getByRole("button", { name: "Yes, capture commitment" });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(mockCreateCommitment).toHaveBeenCalledWith("Review the contract next week.", true);
      expect(screen.getByTestId("commitment-card")).toBeInTheDocument();
    });
  });

  it("handles unsupported commitment gracefully with manual follow-through explanation", async () => {
    mockCreateCommitment.mockResolvedValueOnce({
      detected: true,
      persisted: true,
      commitment: {
        id: "com_unsupported_123",
        title: "Finish project report by Friday afternoon",
        action: "Finish project report by",
        due_at: "2026-10-23T17:00:00+07:00",
        priority: "medium",
        status: "open",
        confidence: 0.95,
      },
    });

    mockHandleCommitment.mockResolvedValueOnce({
      requires_manual_follow_through: true,
      message:
        "This commitment requires manual follow-through. PROMISE does not have an automated action for this task, so it has been preserved as open on your dashboard.",
    });

    render(<AlexaSimulator />);

    const input = screen.getByLabelText("Alexa speech input text fallback");
    fireEvent.change(input, { target: { value: "I'll finish the project report by Friday afternoon." } });

    const sendBtn = screen.getByRole("button", { name: "Send to Alexa+" });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Handle this with PROMISE" })).toBeInTheDocument();
    });

    const handleBtn = screen.getByRole("button", { name: "Handle this with PROMISE" });
    fireEvent.click(handleBtn);

    await waitFor(() => {
      expect(mockHandleCommitment).toHaveBeenCalledWith("com_unsupported_123");
      expect(screen.getByTestId("handle-result")).toBeInTheDocument();
      expect(screen.getByTestId("manual-follow-through-notice")).toBeInTheDocument();
      expect(screen.getByText("Manual Follow-Through")).toBeInTheDocument();
      expect(screen.getByText(/This commitment requires manual follow-through/)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /View on Dashboard/i })).toBeInTheDocument();
    });
  });

  it("displays connection error banner when MCP connection fails", async () => {
    mockConnect.mockImplementation(async () => {
      stateCallback?.({
        status: "error",
        url: "http://127.0.0.1:8001/mcp",
        tools: [],
        error: "Connection refused",
      });
      throw new Error("Connection refused");
    });

    render(<AlexaSimulator />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByText(/MCP Endpoint Unreachable/i)).toBeInTheDocument();
    });
  });
});
