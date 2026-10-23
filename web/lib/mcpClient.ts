import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { authHeaders } from "./auth";

export const DEFAULT_MCP_URL = process.env.NEXT_PUBLIC_MCP_URL || "http://127.0.0.1:8001/mcp";

export type McpToolCallLog = {
  id: string;
  timestamp: string;
  tool: string;
  args: Record<string, unknown>;
  durationMs: number;
  result?: unknown;
  error?: string;
};

export type McpConnectionState = {
  status: "disconnected" | "connecting" | "connected" | "error";
  url: string;
  serverVersion?: string;
  protocolVersion?: string;
  tools: string[];
  error?: string;
};

export type McpCommitmentResult = {
  detected: boolean;
  persisted: boolean;
  needs_confirmation?: boolean;
  commitment?: {
    id: string;
    title: string;
    description: string;
    action: string;
    due_at: string | null;
    priority: string;
    status: string;
    confidence: number;
    contact_id?: string | null;
  } | null;
  source?: {
    id: string;
    source_system: string;
    excerpt: string;
    occurred_at: string;
  } | null;
  contact?: {
    id: string;
    name: string;
    email: string | null;
  } | null;
  reason?: string | null;
  message?: string | null;
};

export type McpHandleResult = {
  action?: {
    id: string;
    type: string;
    status: string;
    payload?: Record<string, unknown>;
  };
  approval?: {
    id: string;
    status: string;
    requested_at?: string;
  };
  commitment?: {
    id: string;
    status: string;
  };
  error?: string;
};

export class PromiseMcpService {
  private client: Client | null = null;
  private transport: StreamableHTTPClientTransport | null = null;
  private logs: McpToolCallLog[] = [];
  private onLogsChanged?: (logs: McpToolCallLog[]) => void;
  private onStateChanged?: (state: McpConnectionState) => void;
  private state: McpConnectionState = {
    status: "disconnected",
    url: DEFAULT_MCP_URL,
    tools: [],
  };

  constructor(
    private mcpUrl: string = DEFAULT_MCP_URL,
    callbacks?: {
      onLogsChanged?: (logs: McpToolCallLog[]) => void;
      onStateChanged?: (state: McpConnectionState) => void;
    }
  ) {
    this.onLogsChanged = callbacks?.onLogsChanged;
    this.onStateChanged = callbacks?.onStateChanged;
  }

  public getState(): McpConnectionState {
    return { ...this.state };
  }

  public getLogs(): McpToolCallLog[] {
    return [...this.logs];
  }

  private updateState(partial: Partial<McpConnectionState>) {
    this.state = { ...this.state, ...partial };
    this.onStateChanged?.(this.state);
  }

  private addLog(log: Omit<McpToolCallLog, "id" | "timestamp">) {
    const entry: McpToolCallLog = {
      ...log,
      id: `log_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
      timestamp: new Date().toISOString(),
    };
    this.logs = [entry, ...this.logs].slice(0, 50);
    this.onLogsChanged?.(this.logs);
  }

  public async connect(): Promise<McpConnectionState> {
    if (this.state.status === "connected" && this.client) {
      return this.state;
    }

    this.updateState({ status: "connecting", error: undefined, url: this.mcpUrl });

    try {
      const headers = authHeaders();
      const parsedUrl = new URL(this.mcpUrl);

      this.transport = new StreamableHTTPClientTransport(parsedUrl, {
        requestInit: {
          headers,
        },
      });

      this.client = new Client({
        name: "promise-alexa-simulator",
        version: "0.1.0",
      });

      await this.client.connect(this.transport);

      const serverVersion = this.client.getServerVersion()?.version || "unknown";
      const protocolVersion = this.client.getNegotiatedProtocolVersion() || "2025-06-18";

      // Discover tools
      const toolList = await this.client.listTools();
      const tools = toolList.tools.map((t) => t.name);

      this.updateState({
        status: "connected",
        serverVersion,
        protocolVersion,
        tools,
        error: undefined,
      });

      this.addLog({
        tool: "system/initialize",
        args: { url: this.mcpUrl },
        durationMs: 0,
        result: { serverVersion, protocolVersion, toolCount: tools.length },
      });

      return this.state;
    } catch (err: unknown) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.updateState({
        status: "error",
        error: errorMsg,
      });
      throw err;
    }
  }

  public async disconnect(): Promise<void> {
    try {
      if (this.client) {
        await this.client.close();
      }
      if (this.transport) {
        await this.transport.close();
      }
    } catch {
      // Ignored during cleanup
    } finally {
      this.client = null;
      this.transport = null;
      this.updateState({ status: "disconnected", tools: [] });
    }
  }

  public async createCommitment(text: string, confirm = false): Promise<McpCommitmentResult> {
    if (!this.client || this.state.status !== "connected") {
      await this.connect();
    }
    if (!this.client) {
      throw new Error("MCP client is not connected");
    }

    const start = performance.now();
    const args = {
      text,
      source_system: "alexa",
      confirm,
    };

    try {
      const rawRes = await this.client.callTool({
        name: "create_commitment",
        arguments: args,
      });
      const durationMs = Math.round(performance.now() - start);

      const structured = (rawRes as { structuredContent?: McpCommitmentResult }).structuredContent;
      let parsed: McpCommitmentResult;

      if (structured) {
        parsed = structured;
      } else {
        const textContent = (rawRes as { content?: Array<{ type: string; text: string }> }).content?.find(
          (c) => c.type === "text"
        )?.text;
        try {
          parsed = textContent ? JSON.parse(textContent) : { detected: false, persisted: false };
        } catch {
          parsed = {
            detected: false,
            persisted: false,
            message: textContent || "No structured result returned",
          };
        }
      }

      this.addLog({
        tool: "create_commitment",
        args,
        durationMs,
        result: parsed,
      });

      return parsed;
    } catch (err: unknown) {
      const durationMs = Math.round(performance.now() - start);
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.addLog({
        tool: "create_commitment",
        args,
        durationMs,
        error: errorMsg,
      });
      throw err;
    }
  }

  public async handleCommitment(commitmentId: string): Promise<McpHandleResult> {
    if (!this.client || this.state.status !== "connected") {
      await this.connect();
    }
    if (!this.client) {
      throw new Error("MCP client is not connected");
    }

    const start = performance.now();
    const args = { commitment_id: commitmentId };

    try {
      const rawRes = await this.client.callTool({
        name: "handle_commitment",
        arguments: args,
      });
      const durationMs = Math.round(performance.now() - start);

      const isError = (rawRes as { isError?: boolean }).isError;
      const textContent = (rawRes as { content?: Array<{ type: string; text: string }> }).content?.find(
        (c) => c.type === "text"
      )?.text;

      let parsed: McpHandleResult = {};
      const structured = (rawRes as { structuredContent?: unknown }).structuredContent;

      if (structured && typeof structured === "object") {
        parsed = structured as McpHandleResult;
      } else if (textContent) {
        try {
          parsed = JSON.parse(textContent);
        } catch {
          parsed = { error: textContent };
        }
      }

      if (isError) {
        parsed.error = parsed.error || textContent || "Failed to handle commitment";
      }

      this.addLog({
        tool: "handle_commitment",
        args,
        durationMs,
        result: parsed,
        error: isError ? parsed.error : undefined,
      });

      return parsed;
    } catch (err: unknown) {
      const durationMs = Math.round(performance.now() - start);
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.addLog({
        tool: "handle_commitment",
        args,
        durationMs,
        error: errorMsg,
      });
      throw err;
    }
  }
}
