from __future__ import annotations

import threading

import pytest
from promise_app import tools
from promise_domain.enums import ActionStatus, ApprovalStatus, CommitmentStatus, IntegrationStatus
from promise_domain.models import IntegrationAccount
from promise_integrations.calendar.mock_provider import MockGoogleCalendarProvider
from promise_integrations.calendar.provider import derive_event_id
from promise_shared.errors import ApprovalRequiredError, ConflictError, DuplicateActionError
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


def _synced_repo_get(repo, monkeypatch, parties: int):
    barrier = threading.Barrier(parties)
    original_get = repo.get
    waited = threading.local()

    def synced_get(*args, **kwargs):
        result = original_get(*args, **kwargs)
        if not getattr(waited, "done", False):
            waited.done = True
            barrier.wait()
        return result

    monkeypatch.setattr(repo, "get", synced_get)
    return original_get


def _run_concurrently(*calls):
    outcomes: dict[int, object] = {}

    def run(i: int, fn) -> None:
        try:
            outcomes[i] = fn()
        except Exception as exc:  # noqa: BLE001
            outcomes[i] = exc

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return outcomes


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


# ---- Reject safety regression tests ---------------------------------------------------------

def test_reject_calendar_event_action_blocks_execution_and_creates_no_event(seeded_ctx):
    """Rejecting a create_calendar_event action:
    1. Sets approval status to REJECTED.
    2. Sets action status to REJECTED.
    3. Blocks subsequent execution (raises ApprovalRequiredError).
    4. Never creates an event in Google Calendar (mock.created_event_ids remains empty).
    5. Re-deciding (approve or reject) is rejected as ConflictError (terminal state).
    6. Audit history is preserved without any invalid action.executed event.
    """
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    action_id = handled["action"].id
    approval_id = handled["approval"].id

    assert handled["action"].type.value == "create_calendar_event"
    assert handled["action"].status == ActionStatus.WAITING_FOR_APPROVAL
    assert handled["approval"].status == ApprovalStatus.PENDING

    # Reject the proposed calendar event
    decided_approval = tools.decide_approval(ctx, workspace_id=ws, approval_id=approval_id, decision="rejected", decided_by=uid)
    assert decided_approval.status == ApprovalStatus.REJECTED
    assert decided_approval.decided_by == uid
    assert decided_approval.decided_at is not None

    action_after = ctx.repos.actions.require(ws, action_id)
    assert action_after.status == ActionStatus.REJECTED

    # Attempting to execute the rejected action MUST fail
    with pytest.raises(ApprovalRequiredError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)

    # Verification: Provider was never called, 0 events created
    assert mock.created_event_ids == []

    # Terminal state: Cannot re-decide as approved
    with pytest.raises(ConflictError):
        tools.decide_approval(ctx, workspace_id=ws, approval_id=approval_id, decision="approved", decided_by=uid)

    # Terminal state: Cannot re-decide as rejected
    with pytest.raises(ConflictError):
        tools.decide_approval(ctx, workspace_id=ws, approval_id=approval_id, decision="rejected", decided_by=uid)

    # Action remains rejected
    assert ctx.repos.actions.require(ws, action_id).status == ActionStatus.REJECTED

    # Audit history is preserved: agent_run.waiting_for_approval exists, action.executed does not
    audit_events = tools.list_audit_events(ctx, workspace_id=ws)
    event_types = {e.event_type for e in audit_events}
    assert "agent_run.waiting_for_approval" in event_types
    assert "action.executed" not in event_types


# ---- Approval governance across unapproved action statuses ----------------------------------

@pytest.mark.parametrize("invalid_status,expected_error", [
    (ActionStatus.WAITING_FOR_APPROVAL, ApprovalRequiredError),
    (ActionStatus.REJECTED, ApprovalRequiredError),
    (ActionStatus.PROPOSED, ApprovalRequiredError),
    (ActionStatus.FAILED, ApprovalRequiredError),
    (ActionStatus.CANCELLED, ApprovalRequiredError),
])
def test_calendar_event_unapproved_statuses_block_execution(seeded_ctx, invalid_status, expected_error):
    """Only explicitly APPROVED actions may execute. Pending (WAITING_FOR_APPROVAL),
    REJECTED, PROPOSED, CANCELLED, and FAILED actions must never execute."""
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    action_id = handled["action"].id

    ctx.repos.actions.update(ws, action_id, lambda a: setattr(a, "status", invalid_status))

    with pytest.raises(expected_error):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)

    assert mock.created_event_ids == []


# ---- Duplicate execution / Idempotency ------------------------------------------------------

def test_sequential_repeated_calendar_execution_never_creates_duplicates(seeded_ctx):
    """Sequential repeated execution requests against an approved calendar action:
    1st execution succeeds and creates the Google Calendar event.
    Subsequent executions immediately raise DuplicateActionError without creating duplicate events.
    Action result, status, and commitment status are preserved.
    """
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    action_id = handled["action"].id

    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)

    first = tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)
    assert first["action"].status == ActionStatus.EXECUTED
    assert first["commitment"].status == CommitmentStatus.COMPLETED
    assert len(mock.created_event_ids) == 1
    event_id = mock.created_event_ids[0]

    # Re-executing 2nd and 3rd time must raise DuplicateActionError
    with pytest.raises(DuplicateActionError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)

    with pytest.raises(DuplicateActionError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)

    # Exactly 1 event created; never duplicated
    assert mock.created_event_ids == [event_id]

    action = ctx.repos.actions.require(ws, action_id)
    assert action.status == ActionStatus.EXECUTED
    assert action.result["id"] == event_id


def test_concurrent_calendar_execution_requests_guarantee_single_event(seeded_ctx, monkeypatch):
    """Under concurrency, multiple threads racing to execute the same approved
    calendar action:
    - Exactly ONE caller acquires the atomic APPROVED -> EXECUTING claim.
    - All losing callers receive DuplicateActionError.
    - The Google Calendar event is created exactly once.
    """
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    action_id = handled["action"].id

    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)

    parties = 4
    original_get = _synced_repo_get(ctx.repos.actions, monkeypatch, parties=parties)

    def execute():
        return tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)

    calls = [execute for _ in range(parties)]
    outcomes = _run_concurrently(*calls)
    monkeypatch.setattr(ctx.repos.actions, "get", original_get)

    successes = [v for v in outcomes.values() if not isinstance(v, Exception)]
    failures = [v for v in outcomes.values() if isinstance(v, Exception)]

    assert len(successes) == 1
    assert len(failures) == parties - 1
    assert all(isinstance(f, DuplicateActionError) for f in failures)
    assert len(mock.created_event_ids) == 1

    action = ctx.repos.actions.require(ws, action_id)
    assert action.status == ActionStatus.EXECUTED
    commitment = ctx.repos.commitments.require(ws, commitment.id)
    assert commitment.status == CommitmentStatus.COMPLETED


def test_concurrent_approve_and_reject_calendar_event(seeded_ctx, monkeypatch):
    """Racing decisions for a calendar event:
    One thread approves while another thread rejects.
    Exactly one decision wins via atomic CAS; the other loses with ConflictError.
    If approve wins, execution creates exactly 1 event.
    If reject wins, execution is blocked and 0 events are created.
    """
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text=COMMITMENT_TEXT)["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    approval_id = handled["approval"].id
    action_id = handled["action"].id

    barrier = threading.Barrier(2)
    original_get = ctx.repos.approvals.get

    def synced_get(*args, **kwargs):
        result = original_get(*args, **kwargs)
        barrier.wait()
        return result

    monkeypatch.setattr(ctx.repos.approvals, "get", synced_get)

    outcomes: dict[str, object] = {}

    def decide(decision: str):
        try:
            outcomes[decision] = tools.decide_approval(ctx, workspace_id=ws, approval_id=approval_id, decision=decision, decided_by=uid)
        except Exception as exc:  # noqa: BLE001
            outcomes[decision] = exc

    t_app = threading.Thread(target=decide, args=("approved",))
    t_rej = threading.Thread(target=decide, args=("rejected",))
    t_app.start()
    t_rej.start()
    t_app.join()
    t_rej.join()
    monkeypatch.setattr(ctx.repos.approvals, "get", original_get)

    final_approval = ctx.repos.approvals.require(ws, approval_id)
    final_action = ctx.repos.actions.require(ws, action_id)

    if final_approval.status == ApprovalStatus.APPROVED:
        assert final_action.status == ActionStatus.APPROVED
        assert isinstance(outcomes["rejected"], ConflictError)
        result = tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)
        assert result["action"].status == ActionStatus.EXECUTED
        assert len(mock.created_event_ids) == 1
    else:
        assert final_approval.status == ApprovalStatus.REJECTED
        assert final_action.status == ActionStatus.REJECTED
        assert isinstance(outcomes["approved"], ConflictError)
        with pytest.raises(ApprovalRequiredError):
            tools.execute_approved_action(ctx, workspace_id=ws, action_id=action_id, actor=uid)
        assert mock.created_event_ids == []

