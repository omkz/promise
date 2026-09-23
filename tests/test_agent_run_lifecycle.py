from __future__ import annotations

from promise_app import tools


def test_agent_run_records_steps_and_audit_trail(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id
    uid = ctx.default_user_id

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    run_id = handled["agent_run"].id

    detail = tools.get_agent_run(ctx, workspace_id=ws, user_id=uid, agent_run_id=run_id)
    step_names = [s.name.value for s in detail["steps"]]
    assert step_names == ["retrieval", "planning", "approval"]
    assert all(s.status.value == "completed" for s in detail["steps"])
    assert detail["run"].status.value == "waiting_for_approval"

    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    detail_after = tools.get_agent_run(ctx, workspace_id=ws, user_id=uid, agent_run_id=run_id)
    step_names_after = [s.name.value for s in detail_after["steps"]]
    assert step_names_after == ["retrieval", "planning", "approval", "execution", "completion"]
    assert detail_after["run"].status.value == "completed"

    events = tools.list_audit_events(ctx, workspace_id=ws)
    event_types = {e.event_type for e in events}
    assert "agent_run.started" in event_types
    assert "agent_run.waiting_for_approval" in event_types
    assert "action.executed" in event_types
    assert all(e.agent_run_id in (run_id, None) for e in events)


def test_planning_failure_marks_run_and_commitment_failed(ctx):
    """No documents seeded -> planning can't find anything relevant -> the run
    fails cleanly instead of silently doing nothing."""
    import pytest
    from promise_agent.steps.planning import PlanningError

    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll send Nobody the thing today."
    )["commitment"]

    with pytest.raises(PlanningError):
        tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    failed = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert failed.status.value == "failed"

    runs = tools.list_agent_runs(ctx, workspace_id=ctx.default_workspace_id)
    assert runs[0].status.value == "failed"
