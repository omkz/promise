import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import ConnectionsPage from "../app/connections/page";
import type { IntegrationAccount } from "../lib/api";

// Mock next/navigation
let currentSearchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  usePathname: () => "/connections",
  useSearchParams: () => currentSearchParams,
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
  listIntegrations: vi.fn(),
  connectIntegration: vi.fn(),
  disconnectIntegration: vi.fn(),
  startGmailConnect: vi.fn(),
  startCalendarConnect: vi.fn(),
}));

import * as api from "../lib/api";
const mockListIntegrations = api.listIntegrations as ReturnType<typeof vi.fn>;
const mockConnectIntegration = api.connectIntegration as ReturnType<typeof vi.fn>;
const mockDisconnectIntegration = api.disconnectIntegration as ReturnType<typeof vi.fn>;
const mockStartGmailConnect = api.startGmailConnect as ReturnType<typeof vi.fn>;
const mockStartCalendarConnect = api.startCalendarConnect as ReturnType<typeof vi.fn>;

const sampleAccounts: IntegrationAccount[] = [
  {
    id: "ia_gmail",
    provider: "gmail",
    account_identifier: "alice@example.com",
    status: "connected",
  },
  {
    id: "ia_cal",
    provider: "google_calendar",
    account_identifier: "alice@example.com",
    status: "connected",
  },
  {
    id: "ia_drive",
    provider: "google_drive",
    account_identifier: "alice@work.com",
    status: "connected",
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  currentSearchParams = new URLSearchParams();
});

describe("ConnectionsPage – loading & error states", () => {
  it("renders loading indicator on mount", async () => {
    mockListIntegrations.mockReturnValue(new Promise(() => {}));

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("loading-indicator")).toBeInTheDocument();
  });

  it("shows error notice when backend is unreachable", async () => {
    mockListIntegrations.mockRejectedValue(new Error("Network Error"));

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("page-notice")).toHaveTextContent(
      "Could not reach the backend — start it on port 8000."
    );
  });
});

describe("ConnectionsPage – successful data display", () => {
  it("displays unconnected state when no accounts exist", async () => {
    mockListIntegrations.mockResolvedValue([]);

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("gmail-card")).toHaveTextContent("Not connected");
    expect(screen.getByTestId("btn-connect-gmail")).toBeInTheDocument();

    expect(screen.getByTestId("calendar-card")).toHaveTextContent("Not connected");
    expect(screen.getByTestId("btn-connect-calendar")).toBeInTheDocument();

    expect(screen.getByTestId("empty-demo-accounts")).toBeInTheDocument();
  });

  it("displays connected Gmail and Calendar accounts with status pills", async () => {
    mockListIntegrations.mockResolvedValue(sampleAccounts);

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("gmail-card")).toHaveTextContent("Connected as: alice@example.com");
    expect(screen.getByTestId("gmail-status")).toHaveTextContent("connected");
    expect(screen.getByTestId("btn-disconnect-gmail")).toBeInTheDocument();

    expect(screen.getByTestId("calendar-card")).toHaveTextContent("Connected as: alice@example.com");
    expect(screen.getByTestId("calendar-status")).toHaveTextContent("connected");
    expect(screen.getByTestId("btn-disconnect-calendar")).toBeInTheDocument();

    // Check demo accounts list
    expect(screen.getByTestId("demo-accounts-list")).toHaveTextContent("google drive");
    expect(screen.getByTestId("demo-accounts-list")).toHaveTextContent("Demo Metadata");
  });

  it("identifies when Calendar needs permission because Gmail is already connected", async () => {
    mockListIntegrations.mockResolvedValue([sampleAccounts[0]]); // only Gmail connected

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("calendar-card")).toHaveTextContent("Permission required");
    expect(screen.getByTestId("calendar-status")).toHaveTextContent("Permission needed");
    expect(screen.getByTestId("btn-connect-calendar")).toHaveTextContent("Authorize Calendar");
  });
});

describe("ConnectionsPage – OAuth callback results", () => {
  it("displays success message when returning with ?gmail=connected", async () => {
    currentSearchParams = new URLSearchParams("gmail=connected");
    mockListIntegrations.mockResolvedValue(sampleAccounts);

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("page-notice")).toHaveTextContent("Gmail connected successfully.");
  });

  it("displays error message when returning with ?gmail=error&reason=denied", async () => {
    currentSearchParams = new URLSearchParams("gmail=error&reason=denied");
    mockListIntegrations.mockResolvedValue([]);

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("page-notice")).toHaveTextContent(
      "Gmail authorization was cancelled or access was denied."
    );
  });

  it("displays success message when returning with ?calendar=connected", async () => {
    currentSearchParams = new URLSearchParams("calendar=connected");
    mockListIntegrations.mockResolvedValue(sampleAccounts);

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("page-notice")).toHaveTextContent("Google Calendar connected successfully.");
  });

  it("displays error message when returning with generic ?calendar=error", async () => {
    currentSearchParams = new URLSearchParams("calendar=error");
    mockListIntegrations.mockResolvedValue([]);

    await act(async () => {
      render(<ConnectionsPage />);
    });

    expect(screen.getByTestId("page-notice")).toHaveTextContent("Couldn't connect Google Calendar. Please try again.");
  });
});

describe("ConnectionsPage – actions & error handling", () => {
  it("handles startGmailConnect failure gracefully", async () => {
    mockListIntegrations.mockResolvedValue([]);
    mockStartGmailConnect.mockRejectedValue(new Error("OAuth service unavailable"));

    await act(async () => {
      render(<ConnectionsPage />);
    });

    fireEvent.click(screen.getByTestId("btn-connect-gmail"));

    await waitFor(() => {
      expect(screen.getByTestId("page-notice")).toHaveTextContent("OAuth service unavailable");
    });
  });

  it("handles startCalendarConnect failure gracefully", async () => {
    mockListIntegrations.mockResolvedValue([]);
    mockStartCalendarConnect.mockRejectedValue(new Error("Calendar service unavailable"));

    await act(async () => {
      render(<ConnectionsPage />);
    });

    fireEvent.click(screen.getByTestId("btn-connect-calendar"));

    await waitFor(() => {
      expect(screen.getByTestId("page-notice")).toHaveTextContent("Calendar service unavailable");
    });
  });

  it("disconnects an account and refreshes", async () => {
    mockListIntegrations.mockResolvedValue(sampleAccounts);
    mockDisconnectIntegration.mockResolvedValue({ ...sampleAccounts[0], status: "disconnected" });

    await act(async () => {
      render(<ConnectionsPage />);
    });

    fireEvent.click(screen.getByTestId("btn-disconnect-gmail"));

    await waitFor(() => {
      expect(mockDisconnectIntegration).toHaveBeenCalledWith("ia_gmail");
      expect(mockListIntegrations).toHaveBeenCalledTimes(2);
    });
  });

  it("handles disconnect error gracefully", async () => {
    mockListIntegrations.mockResolvedValue(sampleAccounts);
    mockDisconnectIntegration.mockRejectedValue(new Error("Failed to revoke session"));

    await act(async () => {
      render(<ConnectionsPage />);
    });

    fireEvent.click(screen.getByTestId("btn-disconnect-gmail"));

    await waitFor(() => {
      expect(screen.getByTestId("page-notice")).toHaveTextContent("Failed to revoke session");
    });
  });

  it("connects simulated test account metadata and resets input", async () => {
    mockListIntegrations.mockResolvedValue([]);
    mockConnectIntegration.mockResolvedValue({
      id: "ia_slack",
      provider: "slack",
      account_identifier: "team@slack.com",
      status: "connected",
    });

    await act(async () => {
      render(<ConnectionsPage />);
    });

    const input = screen.getByTestId("demo-identifier-input");
    fireEvent.change(input, { target: { value: "team@slack.com" } });

    const select = screen.getByTestId("demo-provider-select");
    fireEvent.change(select, { target: { value: "slack" } });

    fireEvent.click(screen.getByTestId("btn-add-demo"));

    await waitFor(() => {
      expect(mockConnectIntegration).toHaveBeenCalledWith("slack", "team@slack.com");
      expect(screen.getByTestId("page-notice")).toHaveTextContent("Demo integration metadata recorded.");
    });
  });

  it("handles simulated test account connection error", async () => {
    mockListIntegrations.mockResolvedValue([]);
    mockConnectIntegration.mockRejectedValue(new Error("Invalid account identifier"));

    await act(async () => {
      render(<ConnectionsPage />);
    });

    const input = screen.getByTestId("demo-identifier-input");
    fireEvent.change(input, { target: { value: "invalid" } });

    fireEvent.click(screen.getByTestId("btn-add-demo"));

    await waitFor(() => {
      expect(screen.getByTestId("page-notice")).toHaveTextContent("Invalid account identifier");
    });
  });
});
