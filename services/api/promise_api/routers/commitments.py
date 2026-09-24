from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_auth import AuthenticatedPrincipal, Permission, require
from promise_domain.models import Commitment
from pydantic import BaseModel

from ..deps import get_context, get_principal

router = APIRouter(prefix="/api/commitments", tags=["commitments"])


class CreateCommitmentBody(BaseModel):
    text: str
    source_system: str = "web"
    source_ref: str | None = None
    occurred_at: str | None = None
    """The source/utterance's own timestamp (ISO 8601, timezone-aware), when the caller
    has one — e.g. an Alexa transcript time. Kept distinct from the server-stamped
    processing time. Falls back to processing time when omitted."""
    confirm: bool = False
    """Set true to capture a below-threshold detection that previously came back
    with `needs_confirmation=True` (see POST /api/commitments response)."""


class UpdateCommitmentBody(BaseModel):
    title: str | None = None
    description: str | None = None
    due_at: str | None = None
    priority: str | None = None
    status: str | None = None
    contact_id: str | None = None


@router.get("")
def list_commitments(
    query: str = "", status: str | None = None, principal: AuthenticatedPrincipal = Depends(get_principal),
    ctx: AppContext = Depends(get_context),
) -> list[Commitment]:
    require(principal, Permission.COMMITMENTS_READ)
    return tools.search_commitments(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, query=query, status=status)


@router.post("")
def create_commitment(
    body: CreateCommitmentBody, response: Response, principal: AuthenticatedPrincipal = Depends(get_principal),
    ctx: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """Detect a commitment from free text. The response always distinguishes
    `detected` (is this a personal commitment at all) from `persisted` (was a
    Commitment row actually written) — see `promise_app.tools.create_commitment`.
    Returns 201 only when a Commitment was created; 200 otherwise (not detected,
    or detected but below the auto-capture confidence threshold)."""
    require(principal, Permission.COMMITMENTS_WRITE)
    result = tools.create_commitment(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, text=body.text,
        source_system=body.source_system, source_ref=body.source_ref, occurred_at=body.occurred_at, confirm=body.confirm,
    )
    response.status_code = 201 if result["persisted"] else 200
    return result


@router.get("/{commitment_id}")
def get_commitment(
    commitment_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> Commitment:
    require(principal, Permission.COMMITMENTS_READ)
    return tools.get_commitment(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id)


@router.get("/{commitment_id}/context")
def get_commitment_context(
    commitment_id: str, limit: int | None = None, principal: AuthenticatedPrincipal = Depends(get_principal),
    ctx: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """Ranked, explainable documents/messages relevant to completing this commitment
    (see `promise_app.tools.retrieve_commitment_context`). Read-only, does not mutate
    the commitment or trigger the agent."""
    require(principal, Permission.CONTEXT_READ)
    return tools.retrieve_commitment_context(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id, limit=limit
    )


@router.patch("/{commitment_id}")
def update_commitment(
    commitment_id: str, body: UpdateCommitmentBody, principal: AuthenticatedPrincipal = Depends(get_principal),
    ctx: AppContext = Depends(get_context),
) -> Commitment:
    require(principal, Permission.COMMITMENTS_WRITE)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return tools.update_commitment(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id, **fields
    )


@router.post("/{commitment_id}/handle")
def handle_commitment(
    commitment_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    """Trigger the agent: retrieve context, plan an action, and request approval.
    This never executes a side effect by itself — see POST /api/actions/{id}/execute."""
    require(principal, Permission.AGENT_EXECUTE)
    return tools.handle_commitment(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id, trigger="api.handle"
    )


@router.post("/{commitment_id}/complete")
def complete_commitment(
    commitment_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> Commitment:
    require(principal, Permission.COMMITMENTS_WRITE)
    return tools.complete_commitment(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id)


@router.post("/{commitment_id}/cancel")
def cancel_commitment(
    commitment_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> Commitment:
    require(principal, Permission.COMMITMENTS_WRITE)
    return tools.cancel_commitment(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, commitment_id=commitment_id)
