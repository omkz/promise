from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv

load_dotenv()

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, TextContent
from promise_app import tools
from promise_app.bootstrap import build_context
from promise_app.identity import authenticate
from promise_auth import AuthenticatedPrincipal, AuthRequest, Permission, require
from promise_domain.models import Commitment, CommitmentSource, Contact
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

"""PROMISE MCP adapter.

Alexa+ (and any other MCP client) is a channel, nothing more: every tool
below is a thin translation from an MCP call to a `promise_app.tools`
function — the exact same application services `services/api` calls. No
business logic lives in this file. Alexa+-specific concerns (voice
phrasing, MCP App UI payloads) stay in this adapter; commitment/approval/
execution logic never does.

Milestone 2 adds an MCP Apps (SEP-1865, io.modelcontextprotocol/ui) view for
`create_commitment`: a `ui://` resource rendering a compact Commitment Card,
whose "Handle this" button calls back into the *same* `handle_commitment`
tool below — the view has no direct access to the domain/database, only to
MCP tool calls that flow through `promise_app.tools` like everything else.
The view itself is built from the standalone package at
`services/mcp/ui/commitment-card` using the official
`@modelcontextprotocol/ext-apps` client/runtime, not a hand-written
postMessage bridge.

Milestone 3 (Identity & Authorization Foundation) removes `workspace_id`/
`user_id`/`actor`/`decided_by` as tool arguments entirely: every tool now
derives an `AuthenticatedPrincipal` from the authenticated MCP request
context (`Context.headers["authorization"]`) via `_authenticate`, the exact
same `promise_app.identity.authenticate` REST uses through
`services/api/promise_api/deps.py`. A tool argument is client-supplied input
— per `mcp.server.mcpserver.context.Context.headers`'s own docstring, "never
treat one as an identity assertion" — so none of them are trusted for
identity anymore, only for content (commitment text, ids being acted on,
...).

Alexa+ MCP account-linking (OAuth authorization code + PKCE, per Alexa+'s MCP
integration model) hands this adapter a standard `Authorization: Bearer
<token>` header once linked — `_authenticate` below is the extension point:
switch `AUTH_MODE` to `oidc` and point `COGNITO_*` at the right issuer/pool,
and Alexa+ bearer tokens authenticate exactly like any other OIDC client, no
code change here. Implementing Alexa+'s actual account-linking deployment
configuration is out of scope for this milestone.
"""

ctx = build_context()
mcp = MCPServer("PROMISE")

UI_RESOURCE_URI = "ui://promise/commitment-card"
# Built by the dedicated MCP App package at services/mcp/ui/commitment-card
# (`npm run build`): a single self-contained HTML file using the official
# @modelcontextprotocol/ext-apps client/runtime, not a hand-written
# postMessage bridge.
_UI_DIST = Path(__file__).parent.parent / "ui" / "commitment-card" / "dist" / "index.html"
_UI_HTML = _UI_DIST.read_text(encoding="utf-8")


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    return value


def _authenticate(mcp_ctx: Context | None) -> AuthenticatedPrincipal:
    """Every tool's one and only identity source. `mcp_ctx` is `None`, or its
    `.headers` raises, when a tool is called without a live bound request —
    directly in-process (e.g. tests calling `mcp_server.create_commitment(...)`
    the way `tests/test_mcp_adapter.py` does) or via `mcp.call_tool(...)` in a
    test with no real transport underneath (`tests/test_mcp_apps_ui.py`). The
    configured `AuthProvider` still runs with no Authorization header either
    way, so `AUTH_MODE=local` behaves exactly as before and `AUTH_MODE=oidc`
    still fails closed (`AuthenticationRequired`), never silently substituting
    local identity.
    """
    headers: dict[str, str] = {}
    if mcp_ctx is not None:
        try:
            headers = dict(mcp_ctx.headers or {})
        except ValueError:
            headers = {}
    authorization = headers.get("authorization") or headers.get("Authorization")
    return authenticate(ctx, AuthRequest(authorization_header=authorization))


@mcp.resource(
    UI_RESOURCE_URI,
    name="commitment_card",
    title="Commitment Card",
    description="Compact card shown after a commitment is captured, with a Handle action.",
    mime_type="text/html;profile=mcp-app",
)
def commitment_card_view() -> str:
    return _UI_HTML


class CreateCommitmentResult(BaseModel):
    """Structured output schema for `create_commitment` (advertised in tools/list).

    `commitment`/`source` are only present when `persisted` is true: the engine
    distinguishes actual personal commitments from suggestions, questions,
    other people's obligations, past actions, etc. (see
    `promise_agent.commitment_extraction`) and never writes a Commitment row
    for anything that isn't one.
    """

    detected: bool
    persisted: bool
    needs_confirmation: bool = False
    commitment: Commitment | None = None
    source: CommitmentSource | None = None
    contact: Contact | None = None
    reason: str | None = None
    message: str | None = None


@mcp.tool(meta={"ui": {"resourceUri": UI_RESOURCE_URI}})
def create_commitment(
    text: str, source_system: str = "alexa", occurred_at: str | None = None, confirm: bool = False,
    mcp_ctx: Context | None = None,
) -> Annotated[CallToolResult, CreateCommitmentResult]:
    """Detect and save a commitment from natural-language text, with provenance.

    Calls the same `promise_app.tools.create_commitment` application service as
    the REST API — no extraction logic lives in this adapter. The tool is
    registered with `_meta.ui.resourceUri` pointing at the Commitment Card view
    (see tools/list); the call result carries the same pointer in its own
    `_meta` only when a commitment was actually captured, so a host renders the
    card only for a real capture rather than for a question/suggestion/etc.
    Pass `occurred_at` (ISO 8601) when the host knows the utterance's own
    timestamp (e.g. a transcript time), distinct from when PROMISE processes it.
    Pass `confirm=true` to capture a prior `needs_confirmation` result anyway.
    """
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.COMMITMENTS_WRITE)
    result = tools.create_commitment(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id,
        text=text, source_system=source_system, occurred_at=occurred_at, confirm=confirm,
    )
    structured = {k: result.get(k) for k in CreateCommitmentResult.model_fields}

    if result["persisted"]:
        commitment: Commitment = result["commitment"]
        return CallToolResult(
            content=[TextContent(type="text", text=f'Commitment captured: "{commitment.title}"')],
            structuredContent=_dump(structured),
            _meta={"ui": {"resourceUri": UI_RESOURCE_URI}},
        )
    message = result["message"] if result["needs_confirmation"] else result["reason"]
    return CallToolResult(content=[TextContent(type="text", text=message)], structuredContent=_dump(structured))


@mcp.tool()
def update_commitment(commitment_id: str, mcp_ctx: Context | None = None, **fields: Any) -> dict[str, Any]:
    """Update mutable fields on a commitment (title, description, due_at, priority, status, contact_id)."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.COMMITMENTS_WRITE)
    return _dump(tools.update_commitment(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id, **fields
    ))


@mcp.tool()
def search_commitments(query: str = "", status: str | None = None, mcp_ctx: Context | None = None) -> list[dict[str, Any]]:
    """Search this workspace's commitments by free-text query and optional status."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.COMMITMENTS_READ)
    return _dump(tools.search_commitments(ctx, workspace_id=principal.workspace_id, query=query, status=status))


@mcp.tool()
def retrieve_commitment_context(commitment_id: str, limit: int | None = None, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Ranked, explainable documents/messages relevant to completing a commitment.
    Calls the same `promise_app.tools.retrieve_commitment_context` application
    service REST uses — no retrieval logic lives in this adapter. Read-only."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.CONTEXT_READ)
    return _dump(tools.retrieve_commitment_context(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id, limit=limit,
    ))


@mcp.tool()
def search_files(query: str, mcp_ctx: Context | None = None) -> list[dict[str, Any]]:
    """Search workspace documents through the connected integration provider."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.CONTEXT_READ)
    return _dump(tools.search_files(ctx, workspace_id=principal.workspace_id, query=query))


@mcp.tool()
def get_file(file_id: str, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Return a workspace document's content."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.CONTEXT_READ)
    return _dump(tools.get_file(ctx, workspace_id=principal.workspace_id, file_id=file_id))


@mcp.tool()
def search_messages(query: str, mcp_ctx: Context | None = None) -> list[dict[str, Any]]:
    """Search messages/email-like notes through the connected integration provider."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.CONTEXT_READ)
    return _dump(tools.search_messages(ctx, workspace_id=principal.workspace_id, query=query))


@mcp.tool()
def get_message(message_id: str, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Return a single message."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.CONTEXT_READ)
    return _dump(tools.get_message(ctx, workspace_id=principal.workspace_id, message_id=message_id))


@mcp.tool()
def prepare_revision(document_id: str, feedback: str, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Prepare a revised document using the given feedback (does not send anything)."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.COMMITMENTS_WRITE)
    return _dump(tools.prepare_revision(ctx, workspace_id=principal.workspace_id, document_id=document_id, feedback=feedback))


@mcp.tool()
def create_draft(contact_id: str, document_id: str, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Create an approval-gated outbound message draft; does not send it."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.COMMITMENTS_WRITE)
    return _dump(tools.create_draft(ctx, workspace_id=principal.workspace_id, contact_id=contact_id, document_id=document_id))


@mcp.tool()
def handle_commitment(commitment_id: str, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Run the agent on a commitment: retrieve context, plan an action, and request approval."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.AGENT_EXECUTE)
    return _dump(tools.handle_commitment(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id,
        trigger="mcp.handle_commitment",
    ))


@mcp.tool()
def request_approval(action_id: str, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Explicitly request approval for a proposed action. Approval is never inferred from silence."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.ACTIONS_APPROVE)
    return _dump(tools.request_approval(ctx, workspace_id=principal.workspace_id, action_id=action_id))


@mcp.tool()
def decide_approval(approval_id: str, decision: str, note: str | None = None, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Approve or reject a pending approval. `decision` is "approved" or "rejected"."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.ACTIONS_APPROVE)
    return _dump(tools.decide_approval(
        ctx, workspace_id=principal.workspace_id, approval_id=approval_id, decision=decision,
        decided_by=principal.user_id, note=note,
    ))


@mcp.tool()
def execute_approved_action(action_id: str, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Execute an approved action. Fails if the action has not been approved, and is
    idempotent: re-calling an already-executed action never sends twice."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.ACTIONS_APPROVE)
    return _dump(tools.execute_approved_action(ctx, workspace_id=principal.workspace_id, action_id=action_id, actor=principal.user_id))


@mcp.tool()
def complete_commitment(commitment_id: str, mcp_ctx: Context | None = None) -> dict[str, Any]:
    """Mark a commitment completed directly (when no agent-executed action is involved)."""
    principal = _authenticate(mcp_ctx)
    require(principal, Permission.COMMITMENTS_WRITE)
    return _dump(tools.complete_commitment(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id))


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "promise-mcp", "mcp": "/mcp"})


app = mcp.streamable_http_app(stateless_http=True, host=os.getenv("HOST", "127.0.0.1"))
allowed_origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins or ["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
