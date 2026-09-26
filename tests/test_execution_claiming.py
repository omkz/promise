from __future__ import annotations

import threading

import pytest
from promise_app import tools
from promise_domain.enums import ActionStatus, IntegrationStatus
from promise_domain.models import IntegrationAccount
from promise_integrations.calendar.mock_provider import MockGoogleCalendarProvider
from promise_integrations.gmail.mock_provider import MockGmailIntegrationProvider
from promise_shared.errors import DuplicateActionError
from promise_shared.ids import new_id

"""Issue 2 -- duplicate action execution. `execute_action`'s atomic APPROVED ->
EXECUTING claim (see promise_agent.steps.execution) is what every test below exercises,
across both action types it applies to (Gmail send, Calendar event creation) -- no
Calendar-specific code changed for this, since the existing deterministic
event-id/409-replay design (see test_calendar_approval_flow.py) was already safe on its
own; what was missing is that two concurrent executions could both reach it at all. See
tests/test_gmail_duplicate_send.py for the additional, Gmail-specific defense-in-depth
layer (the Draft's own DRAFT -> SENDING claim)."""


def _connect_gmail(ctx, *, workspace_id: str, user_id: str, mock: MockGmailIntegrationProvider) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="gmail",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref="gmail:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("gmail", lambda _account: mock)
    return account


def _connect_calendar(ctx, *, workspace_id: str, user_id: str, mock: MockGoogleCalendarProvider) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="google_calendar",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref="cal:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("google_calendar", lambda _account: mock)
    return account


def _run_concurrently(*calls):
    """Run each zero-arg callable in its own thread, return {index: result_or_exception}."""
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


def _synced_repo_get(repo, monkeypatch, parties: int):
    """Force each of `parties` threads to block on its *own first* call to `repo.get`
    until all of them have arrived -- guarantees every racing thread observes the same
    pre-transition status before any of them attempts its claim, without needing every
    thread to make the same *total* number of get() calls (execute_action's call count
    per invocation varies with which branch it takes, so gating every call -- not just
    the first -- risks a barrier party that never arrives). Only that one rendezvous is
    needed: everything downstream of it races for real at the store's own atomic
    compare-and-set, which is what's actually under test."""
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


# ---- 5/6: generic action-level claim ---------------------------------------------------------

def test_concurrent_execution_only_one_caller_acquires_executing(seeded_ctx, monkeypatch):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGmailIntegrationProvider(ctx.repos.drafts)
    _connect_gmail(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)

    original_get = _synced_repo_get(ctx.repos.actions, monkeypatch, parties=2)

    def execute():
        return tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    outcomes = _run_concurrently(execute, execute)
    monkeypatch.setattr(ctx.repos.actions, "get", original_get)

    successes = [v for v in outcomes.values() if not isinstance(v, Exception)]
    failures = [v for v in outcomes.values() if isinstance(v, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DuplicateActionError)
    assert mock.sent_draft_ids == [handled["draft"]["id"]]  # the side effect happened exactly once

    action = ctx.repos.actions.require(ws, handled["action"].id)
    assert action.status == ActionStatus.EXECUTED


def test_replayed_completed_action_does_not_execute_the_side_effect_again(seeded_ctx):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGmailIntegrationProvider(ctx.repos.drafts)
    _connect_gmail(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    with pytest.raises(DuplicateActionError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    assert mock.sent_draft_ids == [handled["draft"]["id"]]  # exactly once, not twice


# ---- 8: concurrent Calendar execution ---------------------------------------------------------

def test_calendar_execution_remains_idempotent_under_concurrency(seeded_ctx, monkeypatch):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGoogleCalendarProvider()
    _connect_calendar(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I need to meet Andi Friday at 2.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    assert handled["action"].type.value == "create_calendar_event"
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)

    original_get = _synced_repo_get(ctx.repos.actions, monkeypatch, parties=2)

    def execute():
        return tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    outcomes = _run_concurrently(execute, execute)
    monkeypatch.setattr(ctx.repos.actions, "get", original_get)

    successes = [v for v in outcomes.values() if not isinstance(v, Exception)]
    failures = [v for v in outcomes.values() if isinstance(v, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DuplicateActionError)
    assert len(mock.created_event_ids) == 1  # never more than one event, even though two
    # execute_approved_action calls raced for the same action
