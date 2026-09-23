from __future__ import annotations

from promise_domain.enums import ActionStatus, ApprovalStatus
from promise_domain.models import Approval
from promise_shared.clock import iso_now
from promise_shared.ids import new_id

from ..context import AgentRepos


def request_approval(action_id: str, workspace_id: str, repos: AgentRepos) -> Approval:
    """Never infer approval from silence: every side-effecting action gets an explicit,
    persisted Approval row that starts PENDING and must be decided by a user."""
    approval = Approval(id=new_id("apr"), workspace_id=workspace_id, action_id=action_id)
    repos.approvals.save(approval)
    repos.actions.update(workspace_id, action_id, lambda a: setattr(a, "status", ActionStatus.WAITING_FOR_APPROVAL))
    return approval


def decide_approval(
    approval_id: str, workspace_id: str, decision: ApprovalStatus, decided_by: str, repos: AgentRepos, note: str | None = None
) -> Approval:
    if decision not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        raise ValueError("decision must be 'approved' or 'rejected'")

    approval = repos.approvals.update(
        workspace_id,
        approval_id,
        lambda a: (
            setattr(a, "status", decision),
            setattr(a, "decided_by", decided_by),
            setattr(a, "decided_at", iso_now()),
            setattr(a, "note", note),
        ),
    )
    new_action_status = ActionStatus.APPROVED if decision == ApprovalStatus.APPROVED else ActionStatus.REJECTED
    repos.actions.update(workspace_id, approval.action_id, lambda a: setattr(a, "status", new_action_status))
    return approval
