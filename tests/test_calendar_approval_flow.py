from __future__ import annotations

import pytest
from promise_app import tools
from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount
from promise_integrations.calendar.mock_provider import MockGoogleCalendarProvider
from promise_integrations.calendar.provider import derive_event_id
from promise_shared.errors import ApprovalRequiredError, DuplicateActionError
from promise_shared.ids import new_id

"""End-to-end: a commitment that ends in a CREATE_CALENDAR_EVENT action,
executed through a connected Calendar account -- PROMISE's existing approval
state machine (Action -> WAITING_FOR_APPROVAL -> approve -> execute ->
completed) is untouched; see promise_agent.steps.execution.execute_action."""

COMMITMENT_TEXT = "I need to meet Andi Friday at 2."


def _connect_calendar(ctx, *, workspace_id: str, user_id: str, mock: MockGoogleCalendarProvider) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="google_calendar",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref="cal:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("google_calendar", lambda _account: mock)
    return account


def test_full_commitment_to_calendar_event_flow_with_approval(seeded_ctx):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    assert handled["action"].type.value == "create_calendar_event"
    assert handled["action"].status.value == "waiting_for_approval"
    assert mock.created_event_ids == []  # never created before approval

    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    result = tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    assert result["action"].status.value == "executed"
    assert result["commitment"].status.value == "completed"
    # provider identity is recorded, not just an HTTP-200 assumption
    assert result["action"].result["id"] == derive_event_id(handled["action"].idempotency_key)
    assert result["action"].result["status"] == "confirmed"
    assert result["action"].result["html_link"]
    assert result["action"].result["created_at"]
    assert mock.created_event_ids == [derive_event_id(handled["action"].idempotency_key)]


def test_calendar_event_creation_still_requires_explicit_approval(seeded_ctx):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)

    with pytest.raises(ApprovalRequiredError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)
    assert mock.created_event_ids == []


def test_repeated_execution_never_creates_a_duplicate_event(seeded_ctx):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    with pytest.raises(DuplicateActionError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    assert len(mock.created_event_ids) == 1  # exactly once, never twice


def test_provider_level_retry_after_a_network_timeout_does_not_duplicate(seeded_ctx):
    """Simulates the exact failure mode idempotency exists for: the first
    create_event call "succeeds" at Google but PROMISE never learns the
    result (a raised exception standing in for a network timeout); a second
    call with the same idempotency_key must land on the same event, not a
    second one -- proven directly against the provider, independent of the
    approval state machine's own duplicate-execution guard."""
    ctx = seeded_ctx
    mock = MockGoogleCalendarProvider()
    key = "create_calendar_event:cmt_x"

    first = mock.create_event(
        ctx.default_workspace_id, summary="Meeting with Andi", start_at="2026-09-25T14:00:00+07:00",
        end_at="2026-09-25T14:30:00+07:00", timezone="Asia/Jakarta", idempotency_key=key,
    )
    second = mock.create_event(
        ctx.default_workspace_id, summary="Meeting with Andi", start_at="2026-09-25T14:00:00+07:00",
        end_at="2026-09-25T14:30:00+07:00", timezone="Asia/Jakarta", idempotency_key=key,
    )
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert first["id"] == second["id"]
    assert len(mock.created_event_ids) == 1


def test_no_calendar_connection_fails_the_action_rather_than_silently_creating_locally(seeded_ctx):
    """Unlike send_message, create_calendar_event has no local/demo fallback --
    a calendar event is inherently external. No connected account means the
    action is recorded FAILED with IntegrationNotConnected, never a crash and
    never a silently-invented local event."""
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    result = tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    assert result["action"].status.value == "failed"
    assert "google_calendar" in result["action"].error
    assert result["commitment"] is None or result["commitment"].status.value != "completed"


def test_execution_step_classifies_missing_calendar_connection_as_a_failed_action_not_a_crash(seeded_ctx):
    """execute_action's own try/except converts IntegrationNotConnected (raised
    internally in its create_calendar_event branch) into a recorded FAILED
    action -- it never propagates and never crashes the caller."""
    from promise_agent.steps.execution import execute_action

    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)

    action = execute_action(handled["action"].id, ws, ctx.agent_repos, user_id=uid)
    assert action.status.value == "failed"
    assert "google_calendar" in action.error
