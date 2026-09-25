from __future__ import annotations

import pytest
from promise_domain.enums import DraftStatus
from promise_domain.models import Draft
from promise_integrations.gmail.mock_provider import MockGmailIntegrationProvider
from promise_shared.errors import IntegrationInvalidRequest, IntegrationNotConnected
from promise_shared.ids import new_id

WORKSPACE = "ws_1"


def _draft(ctx, **overrides):
    draft = Draft(id=new_id("draft"), workspace_id=WORKSPACE, recipient="andi@example.com", subject="Hi", body="Body", **overrides)
    ctx.repos.drafts.save(draft)
    return draft


def test_seeded_messages_are_returned_deterministically(ctx):
    provider = MockGmailIntegrationProvider(ctx.repos.drafts)
    provider.seed_messages([
        {"id": "m1", "workspace_id": WORKSPACE, "subject": "proposal update", "content": "..."},
        {"id": "m2", "workspace_id": WORKSPACE, "subject": "unrelated", "content": "..."},
    ])

    results = provider.search_messages(WORKSPACE, "proposal")
    assert [m["id"] for m in results] == ["m1"]
    assert provider.get_message(WORKSPACE, "m2")["id"] == "m2"
    assert provider.get_message(WORKSPACE, "missing") is None


def test_never_returns_messages_from_another_workspace(ctx):
    provider = MockGmailIntegrationProvider(ctx.repos.drafts)
    provider.seed_messages([{"id": "m1", "workspace_id": "ws_other", "subject": "x", "content": "y"}])

    assert provider.search_messages(WORKSPACE, "") == []
    assert provider.get_message(WORKSPACE, "m1") is None


def test_not_connected_raises_on_every_operation(ctx):
    provider = MockGmailIntegrationProvider(ctx.repos.drafts, connected=False)
    with pytest.raises(IntegrationNotConnected):
        provider.search_messages(WORKSPACE, "")
    with pytest.raises(IntegrationNotConnected):
        provider.get_message(WORKSPACE, "m1")
    with pytest.raises(IntegrationNotConnected):
        provider.send_message(WORKSPACE, draft_id="d1", idempotency_key="k1")


def test_send_message_marks_draft_sent_and_never_contacts_gmail(ctx):
    draft = _draft(ctx)
    provider = MockGmailIntegrationProvider(ctx.repos.drafts)

    result = provider.send_message(WORKSPACE, draft_id=draft.id, idempotency_key="k1")
    assert result["idempotent_replay"] is False
    assert result["draft"]["status"] == "sent"
    assert provider.sent_draft_ids == [draft.id]

    reloaded = ctx.repos.drafts.require(WORKSPACE, draft.id)
    assert reloaded.status == DraftStatus.SENT


def test_send_message_is_idempotent(ctx):
    draft = _draft(ctx, status=DraftStatus.SENT)
    provider = MockGmailIntegrationProvider(ctx.repos.drafts)

    result = provider.send_message(WORKSPACE, draft_id=draft.id, idempotency_key="k1")
    assert result["idempotent_replay"] is True
    assert provider.sent_draft_ids == []  # never "sent" again


def test_v1_scope_boundary_matches_the_real_provider(ctx):
    provider = MockGmailIntegrationProvider(ctx.repos.drafts)
    with pytest.raises(IntegrationInvalidRequest):
        provider.search_files(WORKSPACE, "q")
    with pytest.raises(IntegrationInvalidRequest):
        provider.get_file(WORKSPACE, "f1")
    with pytest.raises(IntegrationInvalidRequest):
        provider.create_draft(WORKSPACE, recipient="a@b.com", subject="s", body="b")
