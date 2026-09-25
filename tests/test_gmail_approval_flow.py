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


def test_full_flow_attaches_the_revised_document_through_real_gmail_provider(seeded_ctx):
    """Same end-to-end flow, but through the real GmailIntegrationProvider (httpx.
    MockTransport-backed) rather than the mock, to prove the planner's revised
    Document actually ends up as a correctly-attached MIME part when it reaches
    Gmail's messages.send -- not just that the mock's send_message was called."""
    import base64
    import email
    import json as _json
    import time

    import httpx
    from promise_integrations.gmail.config import GmailConfig
    from promise_integrations.gmail.provider import GmailIntegrationProvider

    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    secret_ref = "gmail:real:1"
    ctx.secret_store.put_secret(secret_ref, {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})

    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=ws, user_id=uid, provider="gmail",
        account_identifier=f"{uid}@example.com", status=IntegrationStatus.CONNECTED, secret_ref=secret_ref,
    )
    ctx.repos.integration_accounts.save(account)

    sent_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gmail/v1/users/me/messages/send":
            sent_requests.append(request)
            return httpx.Response(200, json={"id": "gmail_real_1", "threadId": "gmail_real_thread_1"})
        raise AssertionError(f"unexpected request to {request.url.path}")

    config = GmailConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600,
    )
    ctx.integrations.register_factory("gmail", lambda acct: GmailIntegrationProvider(
        account_id=acct.id, workspace_id=acct.workspace_id, secret_ref=acct.secret_ref, secret_store=ctx.secret_store,
        config=config, drafts=ctx.repos.drafts, documents=ctx.repos.documents, blob_store=ctx.blob_store,
        transport=httpx.MockTransport(handler),
    ))

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=uid, text="I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    document_id = handled["document"].id
    document_name = handled["document"].name
    document_content = handled["document"].content_text

    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    result = tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    assert result["action"].status.value == "executed"
    assert len(sent_requests) == 1

    body = _json.loads(sent_requests[0].content)
    mime_message = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"].encode("ascii")))
    assert mime_message.is_multipart()
    _body_part, attachment_part = mime_message.get_payload()
    assert attachment_part.get_filename() == document_name
    assert handled["draft"]["attachment_document_id"] == document_id

    # The seeded demo source document is a real DOCX ("Andi_Proposal_v3.docx") --
    # the attached bytes must be a genuine, openable DOCX artifact, never
    # `content_text` renamed with a .docx extension.
    import io

    import docx

    attachment_bytes = attachment_part.get_payload(decode=True)
    assert attachment_part.get_content_type() == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    parsed = docx.Document(io.BytesIO(attachment_bytes))
    reconstructed_text = "\n".join(p.text for p in parsed.paragraphs)
    assert reconstructed_text == document_content


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
