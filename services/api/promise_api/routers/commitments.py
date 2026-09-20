from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_domain.models import Commitment
from pydantic import BaseModel

from ..deps import get_context, user_id, workspace_id

router = APIRouter(prefix="/api/commitments", tags=["commitments"])


class CreateCommitmentBody(BaseModel):
    text: str
    source_system: str = "web"
    source_ref: str | None = None


class UpdateCommitmentBody(BaseModel):
    title: str | None = None
    description: str | None = None
    due_at: str | None = None
    priority: str | None = None
    status: str | None = None
    contact_id: str | None = None


@router.get("")
def list_commitments(
    query: str = "", status: str | None = None, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)
) -> list[Commitment]:
    return tools.search_commitments(ctx, workspace_id=ws, query=query, status=status)


@router.post("", status_code=201)
def create_commitment(
    body: CreateCommitmentBody, ws: str = Depends(workspace_id), uid: str = Depends(user_id), ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    return tools.create_commitment(
        ctx, workspace_id=ws, user_id=uid, text=body.text, source_system=body.source_system, source_ref=body.source_ref
    )


@router.get("/{commitment_id}")
def get_commitment(commitment_id: str, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> Commitment:
    return tools.get_commitment(ctx, workspace_id=ws, commitment_id=commitment_id)


@router.patch("/{commitment_id}")
def update_commitment(
    commitment_id: str, body: UpdateCommitmentBody, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)
) -> Commitment:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return tools.update_commitment(ctx, workspace_id=ws, commitment_id=commitment_id, **fields)


@router.post("/{commitment_id}/handle")
def handle_commitment(
    commitment_id: str, ws: str = Depends(workspace_id), uid: str = Depends(user_id), ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    """Trigger the agent: retrieve context, plan an action, and request approval.
    This never executes a side effect by itself — see POST /api/actions/{id}/execute."""
    return tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment_id, trigger="api.handle")


@router.post("/{commitment_id}/complete")
def complete_commitment(commitment_id: str, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> Commitment:
    return tools.complete_commitment(ctx, workspace_id=ws, commitment_id=commitment_id)


@router.post("/{commitment_id}/cancel")
def cancel_commitment(commitment_id: str, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> Commitment:
    return tools.cancel_commitment(ctx, workspace_id=ws, commitment_id=commitment_id)
