from __future__ import annotations

import threading

from promise_app import tools
from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount
from promise_integrations.gmail.mock_provider import MockGmailIntegrationProvider
from promise_shared.ids import new_id

"""Gmail-specific defense-in-depth (Issue 2's GMAIL section): GmailIntegrationProvider/
MockGmailIntegrationProvider.send_message's own atomic DRAFT -> SENDING claim -- a second,
lower layer below execute_action's Action-level EXECUTING claim (see
tests/test_execution_claiming.py), so send_message stays safe even for a hypothetical
future caller that doesn't go through execute_action's guard at all."""


def _connect_gmail(ctx, *, workspace_id: str, user_id: str, mock: MockGmailIntegrationProvider) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="gmail",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref="gmail:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("gmail", lambda _account: mock)
    return account


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


def _synced_repo_get(repo, monkeypatch, parties: int):
    """See tests/test_execution_claiming.py's own copy of this helper for the full
    rationale -- gates only each thread's first `repo.get` call, not every call."""
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


def test_concurrent_gmail_execution_cannot_send_twice(seeded_ctx, monkeypatch):
    """Through execute_approved_action -- the real, end-to-end path a race would
    actually happen through."""
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
    assert len(successes) == 1
    assert len(mock.sent_draft_ids) == 1  # Gmail's own send API was invoked exactly once
    assert mock.sent_draft_ids == [handled["draft"]["id"]]


def test_gmail_send_message_draft_claim_rejects_a_concurrent_second_caller_directly(seeded_ctx, monkeypatch):
    """Exercises the provider's own DRAFT -> SENDING claim directly (bypassing
    execute_action's Action-level claim entirely), proving send_message is safe on its
    own merits, not just because its one current caller happens to serialize access to
    it -- per the task's explicit "do not rely only on read-status-then-send"."""
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGmailIntegrationProvider(ctx.repos.drafts)
    _connect_gmail(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    draft_id = handled["draft"]["id"]

    original_get = _synced_repo_get(ctx.repos.drafts, monkeypatch, parties=2)

    def send():
        return mock.send_message(ws, draft_id=draft_id, idempotency_key="key-direct")

    outcomes = _run_concurrently(send, send)
    monkeypatch.setattr(ctx.repos.drafts, "get", original_get)

    # Both calls may return without raising: the loser of the DRAFT -> SENDING claim
    # either arrives before the winner has finished (raises, caught by the "already in
    # progress" branch) or after (sees SENT, returns a safe idempotent_replay=True) --
    # both are valid non-exception outcomes. The one invariant that actually matters:
    # Gmail's send API itself was only ever invoked once.
    real_sends = [v for v in outcomes.values() if not isinstance(v, Exception) and v["idempotent_replay"] is False]
    assert len(real_sends) == 1
    assert len(mock.sent_draft_ids) == 1
