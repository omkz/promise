from __future__ import annotations

import pytest
from promise_app import tools
from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount
from promise_integrations.gmail.mock_provider import MockGmailIntegrationProvider
from promise_shared.errors import ApprovalRequiredError, DuplicateActionError
from promise_shared.ids import new_id

"""End-to-end: a commitment that ends in a SEND_MESSAGE action, executed through
a connected Gmail account -- PROMISE's existing approval state machine
(Action -> WAITING_FOR_APPROVAL -> approve -> execute -> completed) is
untouched; Gmail only changes *who* performs the send at execution time (see
promise_agent.steps.execution.execute_action)."""


def _connect_gmail(ctx, *, workspace_id: str, user_id: str, mock: MockGmailIntegrationProvider) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="gmail",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref="gmail:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("gmail", lambda _account: mock)
    return account


def test_full_commitment_to_gmail_send_flow_with_approval(seeded_ctx):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGmailIntegrationProvider(ctx.repos.drafts)
    _connect_gmail(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    assert handled["action"].status.value == "waiting_for_approval"
    assert mock.sent_draft_ids == []  # never sent before approval

    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    result = tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    assert result["action"].status.value == "executed"
    assert result["commitment"].status.value == "completed"
    assert result["action"].result["provider_message_id"].startswith("mock_gmail_msg_")
    assert mock.sent_draft_ids == [handled["draft"]["id"]]


def test_gmail_send_still_requires_explicit_approval(seeded_ctx):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    mock = MockGmailIntegrationProvider(ctx.repos.drafts)
    _connect_gmail(ctx, workspace_id=ws, user_id=uid, mock=mock)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)

    with pytest.raises(ApprovalRequiredError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)
    assert mock.sent_draft_ids == []


def test_repeated_execution_does_not_send_twice(seeded_ctx):
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

    assert mock.sent_draft_ids == [handled["draft"]["id"]]  # exactly once, never twice


def test_no_gmail_connection_falls_back_to_local_provider_for_send(seeded_ctx):
    """No connected Gmail account -> execution still works exactly as it did
    before Gmail existed, via the default (local) provider."""
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    result = tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    assert result["action"].status.value == "executed"
    assert "provider_message_id" not in result["action"].result  # local provider's own result shape
