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
    """PENDING -> APPROVED / PENDING -> REJECTED, and never anything else: both
    APPROVED and REJECTED are terminal. `expected_status=ApprovalStatus.PENDING`
    below makes this an atomic compare-and-set (see `Repository.update`'s own
    docstring) -- a decision only ever succeeds against an approval that is
    *still* PENDING at the moment of the write, not just at the moment we
    happened to read it, so two concurrent `decide_approval` calls for the same
    approval (a double-click, a retried request, an approve and a reject racing
    each other) can never both land: exactly one wins, the other raises
    `ConflictError` -- the existing convention for "this state has already moved
    on" (see `Repository.update`), mapped to HTTP 409 by the API layer already.
    Repeated identical decisions are rejected the same way as a conflicting one,
    not silently treated as a no-op success -- matching `execute_action`'s own
    existing convention for an already-executed action."""
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
        expected_status=ApprovalStatus.PENDING,
    )
    new_action_status = ActionStatus.APPROVED if decision == ApprovalStatus.APPROVED else ActionStatus.REJECTED
    repos.actions.update(workspace_id, approval.action_id, lambda a: setattr(a, "status", new_action_status))
    return approval
