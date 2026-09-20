from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from promise_app import tools
from promise_app.bootstrap import build_context

"""PROMISE MCP adapter.

Alexa+ (and any other MCP client) is a channel, nothing more: every tool
below is a thin translation from an MCP call to a `promise_app.tools`
function — the exact same application services `services/api` calls. No
business logic lives in this file. Alexa+-specific concerns (voice
phrasing, MCP App UI payloads) stay in this adapter; commitment/approval/
execution logic never does.
"""

ctx = build_context()
mcp = MCPServer("PROMISE")


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    return value


@mcp.tool()
def create_commitment(text: str, workspace_id: str | None = None, user_id: str | None = None, source_system: str = "alexa") -> dict:
    """Detect and save a commitment from natural-language text, with provenance."""
    return _dump(tools.create_commitment(
        ctx, workspace_id=workspace_id or ctx.default_workspace_id, user_id=user_id or ctx.default_user_id,
        text=text, source_system=source_system,
    ))


@mcp.tool()
def update_commitment(commitment_id: str, workspace_id: str | None = None, **fields: Any) -> dict:
    """Update mutable fields on a commitment (title, description, due_at, priority, status, contact_id)."""
    return _dump(tools.update_commitment(ctx, workspace_id=workspace_id or ctx.default_workspace_id, commitment_id=commitment_id, **fields))


@mcp.tool()
def search_commitments(query: str = "", status: str | None = None, workspace_id: str | None = None) -> list[dict]:
    """Search this workspace's commitments by free-text query and optional status."""
    return _dump(tools.search_commitments(ctx, workspace_id=workspace_id or ctx.default_workspace_id, query=query, status=status))


@mcp.tool()
def search_files(query: str, workspace_id: str | None = None) -> list[dict]:
    """Search workspace documents through the connected integration provider."""
    return _dump(tools.search_files(ctx, workspace_id=workspace_id or ctx.default_workspace_id, query=query))


@mcp.tool()
def get_file(file_id: str, workspace_id: str | None = None) -> dict:
    """Return a workspace document's content."""
    return _dump(tools.get_file(ctx, workspace_id=workspace_id or ctx.default_workspace_id, file_id=file_id))


@mcp.tool()
def search_messages(query: str, workspace_id: str | None = None) -> list[dict]:
    """Search messages/email-like notes through the connected integration provider."""
    return _dump(tools.search_messages(ctx, workspace_id=workspace_id or ctx.default_workspace_id, query=query))


@mcp.tool()
def get_message(message_id: str, workspace_id: str | None = None) -> dict:
    """Return a single message."""
    return _dump(tools.get_message(ctx, workspace_id=workspace_id or ctx.default_workspace_id, message_id=message_id))


@mcp.tool()
def prepare_revision(document_id: str, feedback: str, workspace_id: str | None = None) -> dict:
    """Prepare a revised document using the given feedback (does not send anything)."""
    return _dump(tools.prepare_revision(ctx, workspace_id=workspace_id or ctx.default_workspace_id, document_id=document_id, feedback=feedback))


@mcp.tool()
def create_draft(contact_id: str, document_id: str, workspace_id: str | None = None) -> dict:
    """Create an approval-gated outbound message draft; does not send it."""
    return _dump(tools.create_draft(ctx, workspace_id=workspace_id or ctx.default_workspace_id, contact_id=contact_id, document_id=document_id))


@mcp.tool()
def handle_commitment(commitment_id: str, workspace_id: str | None = None, user_id: str | None = None) -> dict:
    """Run the agent on a commitment: retrieve context, plan an action, and request approval."""
    return _dump(tools.handle_commitment(
        ctx, workspace_id=workspace_id or ctx.default_workspace_id, user_id=user_id or ctx.default_user_id,
        commitment_id=commitment_id, trigger="mcp.handle_commitment",
    ))


@mcp.tool()
def request_approval(action_id: str, workspace_id: str | None = None) -> dict:
    """Explicitly request approval for a proposed action. Approval is never inferred from silence."""
    return _dump(tools.request_approval(ctx, workspace_id=workspace_id or ctx.default_workspace_id, action_id=action_id))


@mcp.tool()
def decide_approval(approval_id: str, decision: str, decided_by: str | None = None, workspace_id: str | None = None, note: str | None = None) -> dict:
    """Approve or reject a pending approval. `decision` is "approved" or "rejected"."""
    return _dump(tools.decide_approval(
        ctx, workspace_id=workspace_id or ctx.default_workspace_id, approval_id=approval_id, decision=decision,
        decided_by=decided_by or ctx.default_user_id, note=note,
    ))


@mcp.tool()
def execute_approved_action(action_id: str, workspace_id: str | None = None, actor: str | None = None) -> dict:
    """Execute an approved action. Fails if the action has not been approved, and is
    idempotent: re-calling an already-executed action never sends twice."""
    return _dump(tools.execute_approved_action(
        ctx, workspace_id=workspace_id or ctx.default_workspace_id, action_id=action_id, actor=actor or ctx.default_user_id
    ))


@mcp.tool()
def complete_commitment(commitment_id: str, workspace_id: str | None = None) -> dict:
    """Mark a commitment completed directly (when no agent-executed action is involved)."""
    return _dump(tools.complete_commitment(ctx, workspace_id=workspace_id or ctx.default_workspace_id, commitment_id=commitment_id))


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "promise-mcp", "mcp": "/mcp"})


app = mcp.streamable_http_app(stateless_http=True, host=os.getenv("HOST", "127.0.0.1"))
allowed_origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins or ["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
