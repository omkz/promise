from __future__ import annotations

import pytest
from promise_app import tools
from promise_shared.errors import ApprovalRequiredError, DuplicateActionError


def _extract(ctx, text="I'll send Andi the revised proposal tomorrow morning."):
    return tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text=text)


def test_full_lifecycle_open_to_completed(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id
    uid = ctx.default_user_id

    commitment = _extract(ctx)["commitment"]
    assert commitment.status.value == "open"

    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    assert handled["commitment"].status.value == "waiting_for_approval"
    assert handled["action"].status.value == "waiting_for_approval"
    assert handled["approval"].status.value == "pending"
    assert handled["agent_run"].status.value == "waiting_for_approval"

    tools.decide_approval(
        ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid
    )

    result = tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)
    assert result["action"].status.value == "executed"
    assert result["commitment"].status.value == "completed"
    assert result["agent_run"].status.value == "completed"

    fetched = tools.get_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    assert fetched.status.value == "completed"
    assert fetched.completed_at is not None


def test_manual_cancel(ctx):
    commitment = _extract(ctx)["commitment"]
    cancelled = tools.cancel_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert cancelled.status.value == "cancelled"
    assert cancelled.cancelled_at is not None


def test_update_commitment_rejects_unknown_fields(ctx):
    commitment = _extract(ctx)["commitment"]
    with pytest.raises(ValueError):
        tools.update_commitment(
            ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id,
            workspace_id_hack="x",
        )


def test_approval_is_required_before_execution(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id
    commitment = _extract(ctx)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=ctx.default_user_id, commitment_id=commitment.id)

    with pytest.raises(ApprovalRequiredError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=ctx.default_user_id)


def test_rejecting_an_approval_blocks_execution(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id
    uid = ctx.default_user_id
    commitment = _extract(ctx)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)

    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="rejected", decided_by=uid)
    action = tools.list_actions(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)[0]
    assert action.status.value == "rejected"

    with pytest.raises(ApprovalRequiredError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=action.id, actor=uid)


def test_duplicate_execution_is_blocked(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id
    uid = ctx.default_user_id
    commitment = _extract(ctx)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    with pytest.raises(DuplicateActionError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)
