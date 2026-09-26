from __future__ import annotations

import pytest
from promise_app import tools
from promise_domain.enums import ActionStatus, ApprovalStatus
from promise_shared.errors import ConflictError

"""Issue 2 -- Approval/Action consistency. decide_approval() is still two separate
writes (Approval, then Action) -- no distributed transaction, by design (see that
function's own docstring). These tests cover the two things that matter: the happy path
always leaves the pair consistent, and a failure on the *second* write is compensated by
rolling the Approval back to PENDING rather than leaving it terminal while its Action is
still stuck at WAITING_FOR_APPROVAL."""

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


# ---- happy path: the pair always lands together ---------------------------------------------

@pytest.mark.parametrize("decision,expected_action_status", [
    ("approved", ActionStatus.APPROVED),
    ("rejected", ActionStatus.REJECTED),
])
def test_approval_decision_leaves_approval_and_action_states_consistent(seeded_ctx, decision, expected_action_status):
    ctx = seeded_ctx
    handled = _handled(ctx)

    approval = _decide(ctx, handled["approval"].id, decision)

    action = ctx.repos.actions.require(ctx.default_workspace_id, handled["action"].id)
    assert approval.status.value == decision
    assert action.status == expected_action_status


# ---- failure of the second write is handled safely -------------------------------------------

def test_action_transition_exception_rolls_back_the_approval_to_pending(seeded_ctx, monkeypatch):
    ctx = seeded_ctx
    handled = _handled(ctx)

    original_update = ctx.repos.actions.update

    def failing_update(*args, **kwargs):
        raise RuntimeError("simulated storage failure on the action write")

    monkeypatch.setattr(ctx.repos.actions, "update", failing_update)

    with pytest.raises(RuntimeError):
        _decide(ctx, handled["approval"].id, "approved")

    monkeypatch.setattr(ctx.repos.actions, "update", original_update)

    # Rolled all the way back: the pair looks exactly as it did before the failed call.
    approval = ctx.repos.approvals.require(ctx.default_workspace_id, handled["approval"].id)
    action = ctx.repos.actions.require(ctx.default_workspace_id, handled["action"].id)
    assert approval.status == ApprovalStatus.PENDING
    assert approval.decided_by is None
    assert approval.decided_at is None
    assert action.status == ActionStatus.WAITING_FOR_APPROVAL


def test_action_transition_conflict_rolls_back_the_approval_to_pending(seeded_ctx):
    """Same compensation path, but from a real ConflictError (the action's own
    expected_status=WAITING_FOR_APPROVAL condition failing) rather than an arbitrary
    exception -- proves the rollback isn't special-cased to one failure mode."""
    ctx = seeded_ctx
    handled = _handled(ctx)

    # Simulate the action having moved out of WAITING_FOR_APPROVAL some other way --
    # decide_approval's own conditional action-write must now lose its CAS.
    ctx.repos.actions.update(ctx.default_workspace_id, handled["action"].id, lambda a: setattr(a, "status", ActionStatus.CANCELLED))

    with pytest.raises(ConflictError):
        _decide(ctx, handled["approval"].id, "approved")

    approval = ctx.repos.approvals.require(ctx.default_workspace_id, handled["approval"].id)
    assert approval.status == ApprovalStatus.PENDING
    assert approval.decided_by is None
    assert approval.decided_at is None
    # the action itself is untouched by the rollback -- only the approval is compensated
    action = ctx.repos.actions.require(ctx.default_workspace_id, handled["action"].id)
    assert action.status == ActionStatus.CANCELLED


def test_retry_after_a_rolled_back_decision_succeeds_normally(seeded_ctx, monkeypatch):
    """After a compensated failure, the pair is back to PENDING + WAITING_FOR_APPROVAL --
    a fresh decision must work exactly as if the failed attempt never happened."""
    ctx = seeded_ctx
    handled = _handled(ctx)

    original_update = ctx.repos.actions.update
    call_count = {"n": 0}

    def flaky_update(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated one-time storage failure")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(ctx.repos.actions, "update", flaky_update)
    with pytest.raises(RuntimeError):
        _decide(ctx, handled["approval"].id, "approved")

    approval = _decide(ctx, handled["approval"].id, "approved")
    action = ctx.repos.actions.require(ctx.default_workspace_id, handled["action"].id)
    assert approval.status == ApprovalStatus.APPROVED
    assert action.status == ActionStatus.APPROVED
