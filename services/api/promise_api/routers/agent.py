from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_domain.models import Action, AgentRun, Approval, AuditEvent
from pydantic import BaseModel

from ..deps import get_context, user_id, workspace_id

router = APIRouter(prefix="/api", tags=["agent", "approvals", "audit"])


class DecideApprovalBody(BaseModel):
    decision: str  # "approved" | "rejected"
    note: str | None = None


@router.get("/agent-runs")
def list_agent_runs(ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> list[AgentRun]:
    return tools.list_agent_runs(ctx, workspace_id=ws)


@router.get("/agent-runs/{agent_run_id}")
def get_agent_run(agent_run_id: str, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> dict[str, Any]:
    return tools.get_agent_run(ctx, workspace_id=ws, agent_run_id=agent_run_id)


@router.get("/actions")
def list_actions(
    commitment_id: str | None = None, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)
) -> list[Action]:
    return tools.list_actions(ctx, workspace_id=ws, commitment_id=commitment_id)


@router.post("/actions/{action_id}/execute")
def execute_action(
    action_id: str, ws: str = Depends(workspace_id), uid: str = Depends(user_id), ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    return tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)


@router.get("/approvals/pending")
def list_pending_approvals(ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> list[Approval]:
    return tools.list_pending_approvals(ctx, workspace_id=ws)


@router.post("/approvals/{approval_id}/decide")
def decide_approval(
    approval_id: str,
    body: DecideApprovalBody,
    ws: str = Depends(workspace_id),
    uid: str = Depends(user_id),
    ctx: AppContext = Depends(get_context),
) -> Approval:
    """The only place approval is granted. Silence is never treated as approval."""
    return tools.decide_approval(
        ctx, workspace_id=ws, approval_id=approval_id, decision=body.decision, decided_by=uid, note=body.note
    )


@router.get("/audit-events")
def list_audit_events(ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> list[AuditEvent]:
    return tools.list_audit_events(ctx, workspace_id=ws)
