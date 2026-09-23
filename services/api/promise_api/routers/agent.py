from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_auth import AuthenticatedPrincipal, Permission, require
from promise_domain.models import Action, AgentRun, Approval, AuditEvent
from pydantic import BaseModel

from ..deps import get_context, get_principal

router = APIRouter(prefix="/api", tags=["agent", "approvals", "audit"])


class DecideApprovalBody(BaseModel):
    decision: str  # "approved" | "rejected"
    note: str | None = None


@router.get("/agent-runs")
def list_agent_runs(principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)) -> list[AgentRun]:
    require(principal, Permission.COMMITMENTS_READ)
    return tools.list_agent_runs(ctx, workspace_id=principal.workspace_id)


@router.get("/agent-runs/{agent_run_id}")
def get_agent_run(
    agent_run_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    require(principal, Permission.COMMITMENTS_READ)
    return tools.get_agent_run(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, agent_run_id=agent_run_id)


@router.get("/actions")
def list_actions(
    commitment_id: str | None = None, principal: AuthenticatedPrincipal = Depends(get_principal),
    ctx: AppContext = Depends(get_context),
) -> list[Action]:
    require(principal, Permission.COMMITMENTS_READ)
    return tools.list_actions(ctx, workspace_id=principal.workspace_id, commitment_id=commitment_id)


@router.post("/actions/{action_id}/execute")
def execute_action(
    action_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    require(principal, Permission.ACTIONS_APPROVE)
    return tools.execute_approved_action(ctx, workspace_id=principal.workspace_id, action_id=action_id, actor=principal.user_id)


@router.get("/approvals/pending")
def list_pending_approvals(
    principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> list[Approval]:
    require(principal, Permission.COMMITMENTS_READ)
    return tools.list_pending_approvals(ctx, workspace_id=principal.workspace_id)


@router.post("/approvals/{approval_id}/decide")
def decide_approval(
    approval_id: str,
    body: DecideApprovalBody,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    ctx: AppContext = Depends(get_context),
) -> Approval:
    """The only place approval is granted. Silence is never treated as approval."""
    require(principal, Permission.ACTIONS_APPROVE)
    return tools.decide_approval(
        ctx, workspace_id=principal.workspace_id, approval_id=approval_id, decision=body.decision,
        decided_by=principal.user_id, note=body.note,
    )


@router.get("/audit-events")
def list_audit_events(
    principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> list[AuditEvent]:
    require(principal, Permission.COMMITMENTS_READ)
    return tools.list_audit_events(ctx, workspace_id=principal.workspace_id)
