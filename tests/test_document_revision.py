from __future__ import annotations

from promise_app import tools

"""Regression: tools.prepare_revision() must use the same binary-artifact
pipeline (generate_revised_artifact -> blob_ref -> blob_store.put -> Document
with storage_key) as SendRevisedDocumentPlanner, via the shared
promise_agent.document_revision.build_revised_document helper -- never save a
revised Document with content_text only and no attachable artifact."""


def test_prepare_revision_produces_a_document_with_a_stored_binary_artifact(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id

    result = tools.prepare_revision(ctx, workspace_id=ws, document_id="doc_andi_v3", feedback="Tighten the pricing section.")
    document = result["document"]

    assert document.storage_key is not None
    assert document.artifact_size_bytes is not None and document.artifact_size_bytes > 0
    blob = ctx.blob_store.get(document.storage_key)
    assert blob is not None
    assert blob.size_bytes == document.artifact_size_bytes


def test_prepare_revision_on_a_docx_source_produces_a_real_openable_docx(seeded_ctx):
    import io

    import docx

    ctx = seeded_ctx
    ws = ctx.default_workspace_id

    result = tools.prepare_revision(ctx, workspace_id=ws, document_id="doc_andi_v3", feedback="Tighten the pricing section.")
    document = result["document"]

    assert document.type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    blob = ctx.blob_store.get(document.storage_key)
    assert blob.data.startswith(b"PK\x03\x04")  # a real DOCX (ZIP container), never renamed text

    parsed = docx.Document(io.BytesIO(blob.data))
    reconstructed_text = "\n".join(p.text for p in parsed.paragraphs)
    assert reconstructed_text == document.content_text


def test_prepare_revision_preserves_content_text_separately_from_the_artifact(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id

    result = tools.prepare_revision(ctx, workspace_id=ws, document_id="doc_andi_v3", feedback="Tighten the pricing section.")
    document = result["document"]

    assert document.content_text  # extracted text is still there, for retrieval/AI/search
    blob = ctx.blob_store.get(document.storage_key)
    # the artifact is a DOCX container, not the raw extracted text re-encoded
    assert blob.data != document.content_text.encode("utf-8")


def test_prepare_revision_on_a_plain_text_source_produces_a_text_artifact(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id

    result = tools.prepare_revision(ctx, workspace_id=ws, document_id="doc_sarah_feedback", feedback="Summarize this.")
    document = result["document"]

    assert document.type == "text/plain"
    blob = ctx.blob_store.get(document.storage_key)
    assert blob.content_type == "text/plain"
    assert blob.data == document.content_text.encode("utf-8")


def test_prepare_revision_artifact_is_downloadable_via_get_document_artifact(seeded_ctx):
    ctx = seeded_ctx
    ws = ctx.default_workspace_id

    result = tools.prepare_revision(ctx, workspace_id=ws, document_id="doc_andi_v3", feedback="Tighten the pricing section.")
    document = result["document"]

    artifact = tools.get_document_artifact(ctx, workspace_id=ws, document_id=document.id)
    assert artifact["data"].startswith(b"PK\x03\x04")
    assert artifact["filename"] == document.name


def test_prepare_revision_artifact_is_attachable_to_a_gmail_send(seeded_ctx):
    """The whole point of the fix: a document revised via prepare_revision (not
    just the agent's own planner) must be attachable without
    DocumentArtifactMissing."""
    import time

    import httpx
    from promise_domain.enums import IntegrationStatus
    from promise_domain.models import Draft, IntegrationAccount
    from promise_integrations.gmail.config import GmailConfig
    from promise_integrations.gmail.provider import GmailIntegrationProvider
    from promise_shared.ids import new_id

    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id

    result = tools.prepare_revision(ctx, workspace_id=ws, document_id="doc_andi_v3", feedback="Tighten the pricing section.")
    document = result["document"]

    draft = Draft(
        id=new_id("draft"), workspace_id=ws, recipient="andi@example.com", subject="Revised proposal",
        body="Please see attached.", attachment_document_id=document.id,
    )
    ctx.repos.drafts.save(draft)

    secret_ref = "gmail:prepare_revision_test"
    ctx.secret_store.put_secret(secret_ref, {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=ws, user_id=uid, provider="gmail", account_identifier=f"{uid}@example.com",
        status=IntegrationStatus.CONNECTED, secret_ref=secret_ref,
    )
    ctx.repos.integration_accounts.save(account)

    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"id": "gmail_sent_pr", "threadId": "gmail_thread_pr"})

    config = GmailConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600,
    )
    provider = GmailIntegrationProvider(
        account_id=account.id, workspace_id=ws, secret_ref=secret_ref, secret_store=ctx.secret_store,
        config=config, drafts=ctx.repos.drafts, documents=ctx.repos.documents, blob_store=ctx.blob_store,
        transport=httpx.MockTransport(handler),
    )

    result = provider.send_message(ws, draft_id=draft.id, idempotency_key="key1")
    assert result["idempotent_replay"] is False
    assert len(sent) == 1
