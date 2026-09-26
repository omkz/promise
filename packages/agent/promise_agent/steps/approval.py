from __future__ import annotations

import logging

from promise_domain.enums import ActionStatus, ApprovalStatus
from promise_domain.models import Approval
from promise_shared.clock import iso_now
from promise_shared.ids import new_id

from ..context import AgentRepos

logger = logging.getLogger(__name__)


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
    existing convention for an already-executed action.

    Approval/Action consistency: the two rows are still two separate writes (no
    distributed transaction here, by design -- see below), so the Action write
    below can fail independently of the Approval write that already succeeded.
    If it does, this compensates by writing the Approval straight back to
    PENDING (an explicit third write, using the same `expected_status` CAS so
    it can never clobber some other, unrelated change) rather than ever
    persisting an approval in a terminal state whose Action is still stuck at
    WAITING_FOR_APPROVAL: after a failed call, the pair is back to exactly
    PENDING + WAITING_FOR_APPROVAL, as if this call never happened, and a
    caller can safely retry the whole decision from scratch. This is the
    simplest fix that stays inside the existing storage abstraction -- no new
    infrastructure, no multi-item transaction API -- at the cost of a real but
    narrow gap: if the compensating write *itself* fails, the inconsistency
    this whole mechanism exists to prevent can still occur. That residual case
    is logged loudly (see the `except` block) rather than silently swallowed,
    since there is no simpler fix for it that still fits this constraint."""
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
    try:
        repos.actions.update(
            workspace_id, approval.action_id, lambda a: setattr(a, "status", new_action_status),
            expected_status=ActionStatus.WAITING_FOR_APPROVAL,
        )
    except Exception:
        try:
            repos.approvals.update(
                workspace_id,
                approval_id,
                lambda a: (
                    setattr(a, "status", ApprovalStatus.PENDING),
                    setattr(a, "decided_by", None),
                    setattr(a, "decided_at", None),
                    setattr(a, "note", None),
                ),
                expected_status=decision,
            )
        except Exception:
            # The compensating write itself failed -- the approval is now stuck
            # terminal while its action is still WAITING_FOR_APPROVAL. Nothing
            # simpler than this two-write design can close that window without a
            # real multi-item transaction (explicitly out of scope) -- surface it
            # loudly rather than hide it.
            logger.error(
                "decide_approval rollback failed: approval '%s' (action '%s') may be left"
                " inconsistent -- approval decided but action not updated",
                approval_id, approval.action_id,
            )
        raise
    return approval
