import { describe, it, expect, vi, beforeEach } from "vitest";
import { PromiseMcpService } from "../lib/mcpClient";

const mockConnect = vi.fn();
const mockClose = vi.fn();
const mockListTools = vi.fn();
const mockCallTool = vi.fn();
const mockGetServerVersion = vi.fn();
const mockGetNegotiatedProtocolVersion = vi.fn();
const mockTransportClose = vi.fn();

vi.mock("@modelcontextprotocol/client", () => {
  return {
    StreamableHTTPClientTransport: class {
      close = mockTransportClose;
    },
    Client: class {
      connect = mockConnect;
      close = mockClose;
      listTools = mockListTools;
      callTool = mockCallTool;
      getServerVersion = mockGetServerVersion;
      getNegotiatedProtocolVersion = mockGetNegotiatedProtocolVersion;
    },
  };
});

describe("PromiseMcpService", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConnect.mockResolvedValue(undefined);
    mockClose.mockResolvedValue(undefined);
    mockTransportClose.mockResolvedValue(undefined);
    mockGetServerVersion.mockReturnValue({ version: "0.1.0" });
    mockGetNegotiatedProtocolVersion.mockReturnValue("2025-06-18");
    mockListTools.mockResolvedValue({
      tools: [
        { name: "create_commitment", description: "Detect commitment" },
        { name: "handle_commitment", description: "Handle commitment" },
      ],
    });
  });

  it("connects to MCP, discovers tools, and updates state", async () => {
    const onStateChanged = vi.fn();
    const onLogsChanged = vi.fn();
    const service = new PromiseMcpService("http://127.0.0.1:8001/mcp", {
      onStateChanged,
      onLogsChanged,
    });

    const state = await service.connect();

    expect(mockConnect).toHaveBeenCalledTimes(1);
    expect(mockListTools).toHaveBeenCalledTimes(1);
    expect(state.status).toBe("connected");
    expect(state.tools).toEqual(["create_commitment", "handle_commitment"]);
    expect(state.serverVersion).toBe("0.1.0");

    expect(onLogsChanged).toHaveBeenCalled();
    const logs = service.getLogs();
    expect(logs.length).toBeGreaterThan(0);
    expect(logs[0].tool).toBe("system/initialize");
  });

  it("handles connection failure and transitions to error state", async () => {
    mockConnect.mockRejectedValueOnce(new Error("Connection refused"));

    const onStateChanged = vi.fn();
    const service = new PromiseMcpService("http://127.0.0.1:8001/mcp", {
      onStateChanged,
    });

    await expect(service.connect()).rejects.toThrow("Connection refused");

    const state = service.getState();
    expect(state.status).toBe("error");
    expect(state.error).toBe("Connection refused");
  });

  it("calls create_commitment tool with source_system: 'alexa'", async () => {
    mockCallTool.mockResolvedValueOnce({
      content: [{ type: "text", text: 'Commitment captured: "Send proposal"' }],
      structuredContent: {
        detected: true,
        persisted: true,
        commitment: {
          id: "com_123",
          title: "Send proposal",
          action: "Send proposal",
          due_at: "2026-10-23T09:00:00+07:00",
          status: "open",
          confidence: 0.97,
        },
      },
    });

    const service = new PromiseMcpService("http://127.0.0.1:8001/mcp");
    await service.connect();

    const result = await service.createCommitment("I will send Alex the proposal tomorrow.");

    expect(mockCallTool).toHaveBeenCalledWith({
      name: "create_commitment",
      arguments: {
        text: "I will send Alex the proposal tomorrow.",
        source_system: "alexa",
        confirm: false,
      },
    });

    expect(result.detected).toBe(true);
    expect(result.persisted).toBe(true);
    expect(result.commitment?.id).toBe("com_123");
    expect(result.commitment?.title).toBe("Send proposal");
  });

  it("calls handle_commitment tool and receives planned action waiting for approval", async () => {
    mockCallTool.mockResolvedValueOnce({
      structuredContent: {
        commitment: {
          id: "com_123",
          status: "waiting_for_approval",
        },
        action: {
          id: "act_456",
          type: "send_message",
          status: "waiting_for_approval",
        },
        approval: {
          id: "apr_789",
          status: "pending",
        },
      },
    });

    const service = new PromiseMcpService("http://127.0.0.1:8001/mcp");
    await service.connect();

    const result = await service.handleCommitment("com_123");

    expect(mockCallTool).toHaveBeenCalledWith({
      name: "handle_commitment",
      arguments: {
        commitment_id: "com_123",
      },
    });

    expect(result.action?.status).toBe("waiting_for_approval");
    expect(result.action?.type).toBe("send_message");
    expect(result.approval?.id).toBe("apr_789");
  });

  it("disconnects and cleans up resources", async () => {
    const service = new PromiseMcpService("http://127.0.0.1:8001/mcp");
    await service.connect();
    await service.disconnect();

    expect(mockClose).toHaveBeenCalledTimes(1);
    expect(mockTransportClose).toHaveBeenCalledTimes(1);
    expect(service.getState().status).toBe("disconnected");
  });
});
