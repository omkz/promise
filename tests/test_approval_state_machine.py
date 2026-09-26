from __future__ import annotations

import threading

import pytest
from promise_app import tools
from promise_domain.enums import ActionStatus, ApprovalStatus
from promise_shared.errors import ConflictError

"""Approval state machine: PENDING -> APPROVED / PENDING -> REJECTED, both terminal.
`approval_step.decide_approval`'s `expected_status=ApprovalStatus.PENDING` (see that
module) is what makes every transition below a real atomic compare-and-set rather than a
read-then-write race -- see tests/test_conditional_put.py for direct coverage of that
primitive itself."""

EXAMPLE = "I'll send Andi the revised proposal tomorrow morning."


def _handled(ctx):
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text=EXAMPLE
    )["commitment"]
    return tools.handle_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id
    )


def _decide(ctx, approval_id, decision):
    return tools.decide_approval(
        ctx, workspace_id=ctx.default_workspace_id, approval_id=approval_id, decision=decision, decided_by=ctx.default_user_id
    )


# ---- 1/2: the two legal transitions --------------------------------------------------------

def test_pending_to_approved_succeeds(seeded_ctx):
    handled = _handled(seeded_ctx)
    approval = _decide(seeded_ctx, handled["approval"].id, "approved")
    assert approval.status == ApprovalStatus.APPROVED
    action = seeded_ctx.repos.actions.require(seeded_ctx.default_workspace_id, handled["action"].id)
    assert action.status == ActionStatus.APPROVED


def test_pending_to_rejected_succeeds(seeded_ctx):
    handled = _handled(seeded_ctx)
    approval = _decide(seeded_ctx, handled["approval"].id, "rejected")
    assert approval.status == ApprovalStatus.REJECTED
    action = seeded_ctx.repos.actions.require(seeded_ctx.default_workspace_id, handled["action"].id)
    assert action.status == ActionStatus.REJECTED


# ---- 3/4: both are terminal -- neither can be revisited ------------------------------------

def test_approved_cannot_become_rejected(seeded_ctx):
    handled = _handled(seeded_ctx)
    _decide(seeded_ctx, handled["approval"].id, "approved")

    with pytest.raises(ConflictError):
        _decide(seeded_ctx, handled["approval"].id, "rejected")

    approval = seeded_ctx.repos.approvals.require(seeded_ctx.default_workspace_id, handled["approval"].id)
    assert approval.status == ApprovalStatus.APPROVED  # untouched by the rejected second attempt


def test_rejected_cannot_become_approved(seeded_ctx):
    handled = _handled(seeded_ctx)
    _decide(seeded_ctx, handled["approval"].id, "rejected")

    with pytest.raises(ConflictError):
        _decide(seeded_ctx, handled["approval"].id, "approved")

    approval = seeded_ctx.repos.approvals.require(seeded_ctx.default_workspace_id, handled["approval"].id)
    assert approval.status == ApprovalStatus.REJECTED


def test_repeated_identical_decision_is_rejected_not_silently_idempotent(seeded_ctx):
    """Matches execute_action's own existing convention for an already-executed action
    (DuplicateActionError, not a silent no-op success) -- a second identical "approve"
    is a conflict too, not treated as harmless."""
    handled = _handled(seeded_ctx)
    _decide(seeded_ctx, handled["approval"].id, "approved")

    with pytest.raises(ConflictError):
        _decide(seeded_ctx, handled["approval"].id, "approved")


# ---- concurrency: only one of two racing decisions can win ---------------------------------

def test_concurrent_approve_and_reject_only_one_wins(seeded_ctx, monkeypatch):
    handled = _handled(seeded_ctx)
    barrier = threading.Barrier(2)
    original_get = seeded_ctx.repos.approvals.get

    def synced_get(*args, **kwargs):
        result = original_get(*args, **kwargs)
        barrier.wait()
        return result

    monkeypatch.setattr(seeded_ctx.repos.approvals, "get", synced_get)

    outcomes: dict[str, object] = {}

    def run(name: str, decision: str) -> None:
        try:
            outcomes[name] = _decide(seeded_ctx, handled["approval"].id, decision)
        except Exception as exc:  # noqa: BLE001
            outcomes[name] = exc

    t1 = threading.Thread(target=run, args=("approve", "approved"))
    t2 = threading.Thread(target=run, args=("reject", "rejected"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    monkeypatch.setattr(seeded_ctx.repos.approvals, "get", original_get)  # both racers are done; the
    # barrier has no second party left to pair with for the assertions below, which read normally

    successes = [v for v in outcomes.values() if not isinstance(v, Exception)]
    failures = [v for v in outcomes.values() if isinstance(v, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ConflictError)

    final = seeded_ctx.repos.approvals.require(seeded_ctx.default_workspace_id, handled["approval"].id)
    assert final.status in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED)
    # the action's status agrees with whichever decision actually won
    action = seeded_ctx.repos.actions.require(seeded_ctx.default_workspace_id, handled["action"].id)
    expected_action_status = ActionStatus.APPROVED if final.status == ApprovalStatus.APPROVED else ActionStatus.REJECTED
    assert action.status == expected_action_status
