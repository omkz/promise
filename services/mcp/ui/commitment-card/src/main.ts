import { App, applyDocumentTheme, type McpUiToolResultNotification } from "@modelcontextprotocol/ext-apps";

/**
 * PROMISE Commitment Card — MCP App View.
 *
 * Rendered by the host for `ui://promise/commitment-card` after
 * `create_commitment` runs. Talks to the host exclusively through the
 * official MCP Apps client/runtime (`App`): it never touches storage,
 * files, or application code directly — only `create_commitment`'s tool
 * result and a `handle_commitment` call proxied through the host.
 */

interface Commitment {
  id: string;
  title?: string;
  action?: string;
  description?: string;
  due_at?: string | null;
  status: string;
}

interface Contact {
  name?: string;
}

interface CommitmentSource {
  excerpt?: string;
}

interface CreateCommitmentStructuredContent {
  commitment: Commitment;
  contact?: Contact | null;
  source?: CommitmentSource;
}

const titleEl = document.getElementById("title") as HTMLParagraphElement;
const contactEl = document.getElementById("contact") as HTMLSpanElement;
const dueEl = document.getElementById("due") as HTMLSpanElement;
const excerptEl = document.getElementById("excerpt") as HTMLDivElement;
const handleBtn = document.getElementById("handle") as HTMLButtonElement;
const statusEl = document.getElementById("status") as HTMLDivElement;

let commitmentId: string | null = null;

function fmtDue(iso: string | null | undefined): string {
  if (!iso) return "No deadline";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function renderCommitment(structured: CreateCommitmentStructuredContent | undefined) {
  const commitment = structured?.commitment;
  if (!commitment) return;

  commitmentId = commitment.id;
  titleEl.textContent = commitment.title ?? commitment.action ?? "Untitled commitment";
  contactEl.textContent = structured?.contact?.name ?? "—";
  dueEl.textContent = fmtDue(commitment.due_at);
  excerptEl.textContent = "“" + (structured?.source?.excerpt ?? commitment.description ?? "") + "”";

  handleBtn.disabled = false;
}

const app = new App(
  { name: "promise-commitment-card", version: "0.1.0" },
  { availableDisplayModes: ["inline"] },
);

// Register before connect() so no notification is missed (the host may send
// the initial tool-result notification immediately after the handshake).
app.addEventListener("toolresult", (params: McpUiToolResultNotification["params"]) => {
  renderCommitment(params.structuredContent as CreateCommitmentStructuredContent | undefined);
});

app.onhostcontextchanged = (ctx) => {
  if (ctx.theme) applyDocumentTheme(ctx.theme);
};

async function handleClick() {
  if (!commitmentId) return;
  handleBtn.disabled = true;
  statusEl.className = "status";
  statusEl.textContent = "Asking the agent to handle it…";
  try {
    const result = await app.callServerTool({
      name: "handle_commitment",
      arguments: { commitment_id: commitmentId },
    });
    if (result.isError) {
      throw new Error(
        result.content?.find((c) => c.type === "text")?.text ?? "Handle failed",
      );
    }
    const action = (result.structuredContent as { action?: { status?: string } } | undefined)?.action;
    statusEl.className = "status ok";
    statusEl.textContent = action
      ? `Action proposed (${action.status}) — waiting for your approval.`
      : "Agent run started.";
  } catch (err) {
    statusEl.className = "status err";
    statusEl.textContent = "Could not handle this commitment: " + (err as Error).message;
    handleBtn.disabled = false;
  }
}

handleBtn.addEventListener("click", handleClick);

(async function init() {
  try {
    await app.connect();
    const theme = app.getHostContext()?.theme;
    if (theme) applyDocumentTheme(theme);
  } catch (err) {
    statusEl.textContent = "Failed to initialize: " + (err as Error).message;
  }
})();
