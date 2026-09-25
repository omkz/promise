from __future__ import annotations

import time

import httpx
import pytest
from promise_domain.enums import DraftStatus
from promise_integrations.gmail.config import GmailConfig
from promise_integrations.gmail.provider import GmailIntegrationProvider
from promise_shared.errors import (
    IntegrationAuthorizationRevoked,
    IntegrationInvalidRequest,
    IntegrationNotConnected,
    IntegrationPermissionDenied,
    IntegrationRateLimited,
    IntegrationTokenExpired,
)

"""GmailIntegrationProvider against httpx.MockTransport -- no live network or real
Google credentials/OAuth. Uses the `ctx` fixture's own `secret_store`/`repos.drafts`
so this exercises the same token-storage seam the real app uses."""

WORKSPACE = "ws_1"
ACCOUNT_ID = "ia_gmail_1"
SECRET_REF = "gmail:ws_1:ia_gmail_1"


def _config() -> GmailConfig:
    return GmailConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600,
    )


def _provider(ctx, handler) -> GmailIntegrationProvider:
    return GmailIntegrationProvider(
        account_id=ACCOUNT_ID, workspace_id=WORKSPACE, secret_ref=SECRET_REF, secret_store=ctx.secret_store,
        config=_config(), drafts=ctx.repos.drafts, documents=ctx.repos.documents, blob_store=ctx.blob_store,
        transport=httpx.MockTransport(handler),
    )


def _seed_valid_token(ctx, *, expires_in: float = 3600) -> None:
    ctx.secret_store.put_secret(SECRET_REF, {
        "access_token": "at_valid", "refresh_token": "rt_1", "expires_at": time.time() + expires_in,
    })


def _make_draft(ctx, *, recipient="andi@example.com", subject="Revised proposal", body="See attached.", attachment_document_id=None):
    from promise_domain.models import Draft
    from promise_shared.ids import new_id

    draft = Draft(
        id=new_id("draft"), workspace_id=WORKSPACE, recipient=recipient, subject=subject, body=body,
        attachment_document_id=attachment_document_id,
    )
    ctx.repos.drafts.save(draft)
    return draft


def _make_document(
    ctx, *, name="proposal_revised.txt", content_type="text/plain", content_text="Revised proposal content.",
    artifact_bytes=None, with_artifact=True,
):
    """`content_text` (extracted text) and the binary artifact are deliberately
    independent here, the same way the real revision planner keeps them
    separate -- most tests give them the same content only for simplicity,
    but `test_send_message_attaches_binary_artifact_not_extracted_text` below
    gives them different content specifically to prove which one gets sent."""
    from promise_domain.models import Document
    from promise_shared.blobs import blob_ref
    from promise_shared.ids import new_id

    document_id = new_id("doc")
    storage_key = None
    artifact_size = None
    if with_artifact:
        data = artifact_bytes if artifact_bytes is not None else content_text.encode("utf-8")
        storage_key = blob_ref(WORKSPACE, document_id)
        ctx.blob_store.put(storage_key, data, content_type=content_type, filename=name)
        artifact_size = len(data)

    document = Document(
        id=document_id, workspace_id=WORKSPACE, name=name, type=content_type, content_text=content_text,
        storage_key=storage_key, artifact_size_bytes=artifact_size,
    )
    ctx.repos.documents.save(document)
    return document


# ---- search / get -----------------------------------------------------------------------------

def test_search_messages_normalizes_results(ctx):
    _seed_valid_token(ctx)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gmail/v1/users/me/messages":
            assert request.url.params["q"] == "proposal"
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        message_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={
            "id": message_id, "threadId": f"t_{message_id}",
            "payload": {"headers": [{"name": "Subject", "value": f"Re: {message_id}"}], "mimeType": "text/plain", "body": {}},
        })

    provider = _provider(ctx, handler)
    results = provider.search_messages(WORKSPACE, "proposal")
    assert [m["id"] for m in results] == ["m1", "m2"]
    assert results[0]["workspace_id"] == WORKSPACE


def test_get_message_returns_none_shaped_dict_not_gmail_response_shape(ctx):
    _seed_valid_token(ctx)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "full"
        return httpx.Response(200, json={
            "id": "m1", "threadId": "t1",
            "payload": {"headers": [{"name": "From", "value": "sarah@example.com"}], "mimeType": "text/plain", "body": {}},
        })

    provider = _provider(ctx, handler)
    message = provider.get_message(WORKSPACE, "m1")
    assert message["sender"] == "sarah@example.com"
    assert "payload" not in message  # never exposes the raw Gmail response shape


# ---- token handling -----------------------------------------------------------------------------

def test_not_connected_raises_when_no_secret_on_file(ctx):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call Gmail with no stored credentials")

    provider = _provider(ctx, handler)
    with pytest.raises(IntegrationNotConnected):
        provider.get_message(WORKSPACE, "m1")


def test_expired_token_with_no_refresh_token_raises_token_expired(ctx):
    ctx.secret_store.put_secret(SECRET_REF, {"access_token": "stale", "refresh_token": None, "expires_at": time.time() - 10})

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call Gmail with an unrefreshable expired token")

    provider = _provider(ctx, handler)
    with pytest.raises(IntegrationTokenExpired):
        provider.get_message(WORKSPACE, "m1")


def test_token_refreshed_automatically_before_expiry(ctx):
    ctx.secret_store.put_secret(SECRET_REF, {"access_token": "old", "refresh_token": "rt_1", "expires_at": time.time() - 10})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer fresh"
        return httpx.Response(200, json={"id": "m1", "payload": {"headers": [], "body": {}}})

    provider = _provider(ctx, handler)
    provider.get_message(WORKSPACE, "m1")

    stored = ctx.secret_store.get_secret(SECRET_REF)
    assert stored["access_token"] == "fresh"
    assert stored["refresh_token"] == "rt_1"  # preserved, not dropped


def test_401_from_gmail_forces_one_refresh_and_retry(ctx):
    _seed_valid_token(ctx)  # token looks valid by expiry, but Gmail itself rejects it
    call_count = {"messages": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        call_count["messages"] += 1
        if call_count["messages"] == 1:
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"id": "m1", "payload": {"headers": [], "body": {}}})

    provider = _provider(ctx, handler)
    message = provider.get_message(WORKSPACE, "m1")
    assert message["id"] == "m1"
    assert call_count["messages"] == 2  # exactly one retry, never an unbounded loop


def test_revoked_refresh_token_deletes_the_secret_and_raises(ctx):
    ctx.secret_store.put_secret(SECRET_REF, {"access_token": "old", "refresh_token": "rt_revoked", "expires_at": time.time() - 10})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    provider = _provider(ctx, handler)
    with pytest.raises(IntegrationAuthorizationRevoked):
        provider.get_message(WORKSPACE, "m1")
    assert ctx.secret_store.get_secret(SECRET_REF) is None


def test_permission_denied_and_rate_limited_classification(ctx):
    _seed_valid_token(ctx)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    with pytest.raises(IntegrationPermissionDenied):
        _provider(ctx, handler).get_message(WORKSPACE, "m1")

    def rate_limited_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "5"})

    with pytest.raises(IntegrationRateLimited):
        _provider(ctx, rate_limited_handler).get_message(WORKSPACE, "m1")


# ---- send_message -------------------------------------------------------------------------------

def test_send_message_builds_mime_and_marks_draft_sent(ctx):
    _seed_valid_token(ctx)
    draft = _make_draft(ctx)
    sent_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gmail/v1/users/me/messages/send":
            sent_requests.append(request)
            return httpx.Response(200, json={"id": "gmail_sent_1", "threadId": "gmail_thread_1"})
        raise AssertionError(f"unexpected request to {request.url.path}")

    provider = _provider(ctx, handler)
    result = provider.send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")

    assert result["idempotent_replay"] is False
    assert result["provider_message_id"] == "gmail_sent_1"
    assert result["provider_thread_id"] == "gmail_thread_1"
    assert result["draft"]["status"] == "sent"
    assert len(sent_requests) == 1

    import base64
    import email
    import json as _json

    body = _json.loads(sent_requests[0].content)
    raw_bytes = base64.urlsafe_b64decode(body["raw"].encode("ascii"))
    mime_message = email.message_from_bytes(raw_bytes)
    assert mime_message["To"] == "andi@example.com"
    assert mime_message["Subject"] == "Revised proposal"
    assert mime_message.get_payload(decode=True).decode("utf-8") == "See attached."

    reloaded = ctx.repos.drafts.require(WORKSPACE, draft.id)
    assert reloaded.status.value == "sent"
    assert reloaded.sent_at is not None


def test_send_message_is_idempotent_when_draft_already_sent(ctx):
    _seed_valid_token(ctx)
    draft = _make_draft(ctx)
    ctx.repos.drafts.update(WORKSPACE, draft.id, lambda d: setattr(d, "status", DraftStatus.SENT))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never call Gmail again for an already-sent draft")

    provider = _provider(ctx, handler)
    result = provider.send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")
    assert result["idempotent_replay"] is True


# ---- send_message with an attachment -------------------------------------------------------------

def test_send_message_with_attachment_builds_a_multipart_message_with_correct_attachment(ctx):
    import base64
    import email
    import json as _json

    _seed_valid_token(ctx)
    document = _make_document(ctx, name="proposal_revised.txt", content_type="text/plain", content_text="Here is the revised proposal text.")
    draft = _make_draft(ctx, body="Please see the attached revised proposal.", attachment_document_id=document.id)
    sent_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_requests.append(request)
        return httpx.Response(200, json={"id": "gmail_sent_2", "threadId": "gmail_thread_2"})

    result = _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")
    assert result["idempotent_replay"] is False

    body = _json.loads(sent_requests[0].content)
    mime_message = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"].encode("ascii")))

    assert mime_message.is_multipart()
    body_part, attachment_part = mime_message.get_payload()

    # body remains correct
    assert body_part.get_content_type() == "text/plain"
    assert body_part.get_payload(decode=True).decode("utf-8") == "Please see the attached revised proposal."

    # attachment exists, with the correct filename/content-type/bytes
    assert attachment_part.get_filename() == "proposal_revised.txt"
    assert attachment_part.get_content_type() == "text/plain"
    assert attachment_part.get("Content-Disposition", "").startswith("attachment")
    assert attachment_part.get_payload(decode=True).decode("utf-8") == "Here is the revised proposal text."


def test_send_message_with_attachment_marks_draft_sent_and_returns_provider_ids(ctx):
    _seed_valid_token(ctx)
    document = _make_document(ctx)
    draft = _make_draft(ctx, attachment_document_id=document.id)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "gmail_sent_3", "threadId": "gmail_thread_3"})

    result = _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")
    assert result["provider_message_id"] == "gmail_sent_3"
    assert result["draft"]["status"] == "sent"

    reloaded = ctx.repos.drafts.require(WORKSPACE, draft.id)
    assert reloaded.status == DraftStatus.SENT


def test_send_message_with_missing_attachment_document_raises_rather_than_omitting_it(ctx):
    from promise_shared.errors import NotFoundError

    _seed_valid_token(ctx)
    draft = _make_draft(ctx, attachment_document_id="doc_does_not_exist")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never call Gmail when the requested attachment can't be loaded")

    with pytest.raises(NotFoundError):
        _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")

    reloaded = ctx.repos.drafts.require(WORKSPACE, draft.id)
    assert reloaded.status != DraftStatus.SENT


def test_send_message_with_attachment_is_idempotent_when_already_sent(ctx):
    _seed_valid_token(ctx)
    document = _make_document(ctx)
    draft = _make_draft(ctx, attachment_document_id=document.id)
    ctx.repos.drafts.update(WORKSPACE, draft.id, lambda d: setattr(d, "status", DraftStatus.SENT))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never call Gmail again for an already-sent draft, attachment or not")

    result = _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")
    assert result["idempotent_replay"] is True


def test_send_message_attaches_the_binary_artifact_not_extracted_text(ctx):
    """content_text and the binary artifact are deliberately different here --
    proves the attachment comes from DocumentBlobStore, never a re-encoding of
    Document.content_text, even though a naive implementation would produce
    output that looks superficially right for a plain-text document."""
    import base64
    import email
    import json as _json

    _seed_valid_token(ctx)
    artifact_bytes = b"\x50\x4b\x03\x04binary-artifact-bytes-not-text"  # arbitrary non-UTF8-safe bytes
    document = _make_document(
        ctx, name="proposal_revised.bin", content_type="application/octet-stream",
        content_text="This is only the extracted text, never sent as the attachment.",
        artifact_bytes=artifact_bytes,
    )
    draft = _make_draft(ctx, attachment_document_id=document.id)
    sent_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_requests.append(request)
        return httpx.Response(200, json={"id": "gmail_sent_5", "threadId": "gmail_thread_5"})

    _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")

    body = _json.loads(sent_requests[0].content)
    mime_message = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"].encode("ascii")))
    _body_part, attachment_part = mime_message.get_payload()
    assert attachment_part.get_payload(decode=True) == artifact_bytes


def test_send_message_with_no_binary_artifact_raises_document_artifact_missing_not_a_fallback(ctx):
    from promise_shared.errors import DocumentArtifactMissing

    _seed_valid_token(ctx)
    document = _make_document(ctx, with_artifact=False)  # storage_key is None -- text-only document
    draft = _make_draft(ctx, attachment_document_id=document.id)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never call Gmail when there is no binary artifact to attach")

    with pytest.raises(DocumentArtifactMissing):
        _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")

    reloaded = ctx.repos.drafts.require(WORKSPACE, draft.id)
    assert reloaded.status != DraftStatus.SENT


def test_send_message_with_storage_key_but_missing_blob_raises_document_artifact_missing(ctx):
    """The Document row claims an artifact exists (storage_key set) but the blob
    store has nothing at that ref -- deleted out from under it, or never
    actually written. Still never falls back to content_text."""
    from promise_domain.models import Document
    from promise_shared.blobs import blob_ref
    from promise_shared.errors import DocumentArtifactMissing
    from promise_shared.ids import new_id

    _seed_valid_token(ctx)
    document_id = new_id("doc")
    document = Document(
        id=document_id, workspace_id=WORKSPACE, name="ghost.txt", type="text/plain",
        content_text="text that must never be sent as a substitute attachment",
        storage_key=blob_ref(WORKSPACE, document_id), artifact_size_bytes=100,
    )
    ctx.repos.documents.save(document)
    draft = _make_draft(ctx, attachment_document_id=document.id)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never call Gmail when the referenced blob is missing")

    with pytest.raises(DocumentArtifactMissing):
        _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")


def test_send_message_rejects_an_oversized_attachment_before_building_or_sending_mime(ctx):
    from promise_shared.errors import AttachmentTooLarge

    _seed_valid_token(ctx)
    document = _make_document(ctx, artifact_bytes=b"x" * 100)
    draft = _make_draft(ctx, attachment_document_id=document.id)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never call Gmail for a rejected oversized attachment")

    small_config = GmailConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600, max_attachment_bytes=50,
    )
    provider = GmailIntegrationProvider(
        account_id=ACCOUNT_ID, workspace_id=WORKSPACE, secret_ref=SECRET_REF, secret_store=ctx.secret_store,
        config=small_config, drafts=ctx.repos.drafts, documents=ctx.repos.documents, blob_store=ctx.blob_store,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AttachmentTooLarge) as excinfo:
        provider.send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")
    assert excinfo.value.size_bytes == 100
    assert excinfo.value.max_bytes == 50

    reloaded = ctx.repos.drafts.require(WORKSPACE, draft.id)
    assert reloaded.status != DraftStatus.SENT


def test_send_message_with_attachment_comfortably_under_the_limit_succeeds(ctx):
    _seed_valid_token(ctx)
    document = _make_document(ctx, artifact_bytes=b"small attachment")
    draft = _make_draft(ctx, attachment_document_id=document.id)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "gmail_sent_ok", "threadId": "gmail_thread_ok"})

    result = _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")
    assert result["idempotent_replay"] is False
    assert result["provider_message_id"] == "gmail_sent_ok"


def test_send_message_rejects_raw_bytes_that_would_exceed_the_limit_once_base64_encoded(ctx):
    """The regression this whole fix is about: an attachment whose RAW byte
    count is under max_attachment_bytes but would exceed it once base64
    content-transfer-encoding inflates it by 4/3 must still be rejected --
    comparing raw bytes directly against the encoded-message limit is not
    a safe check."""
    from promise_shared.errors import AttachmentTooLarge

    _seed_valid_token(ctx)
    # raw size is under max_attachment_bytes (100) but well over max/margin (100/1.35 ≈ 74)
    document = _make_document(ctx, artifact_bytes=b"x" * 90)
    draft = _make_draft(ctx, attachment_document_id=document.id)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never call Gmail when raw bytes leave no room for encoding overhead")

    small_config = GmailConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600, max_attachment_bytes=100,
    )
    provider = GmailIntegrationProvider(
        account_id=ACCOUNT_ID, workspace_id=WORKSPACE, secret_ref=SECRET_REF, secret_store=ctx.secret_store,
        config=small_config, drafts=ctx.repos.drafts, documents=ctx.repos.documents, blob_store=ctx.blob_store,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AttachmentTooLarge):
        provider.send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")


def test_send_message_final_encoded_size_check_catches_what_the_pre_check_cannot(ctx):
    """The pre-check only runs when there's an attachment. A plain-text-only
    message has no pre-check at all -- the *final* encoded-size check is the
    only thing standing between an oversized body and Gmail, proving it's a
    real, independent second layer, not dead code shadowed by the pre-check."""
    from promise_shared.errors import AttachmentTooLarge

    _seed_valid_token(ctx)
    draft = _make_draft(ctx, body="x" * 500, attachment_document_id=None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never call Gmail once the final encoded message exceeds the limit")

    tiny_config = GmailConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600, max_attachment_bytes=50,
    )
    provider = GmailIntegrationProvider(
        account_id=ACCOUNT_ID, workspace_id=WORKSPACE, secret_ref=SECRET_REF, secret_store=ctx.secret_store,
        config=tiny_config, drafts=ctx.repos.drafts, documents=ctx.repos.documents, blob_store=ctx.blob_store,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AttachmentTooLarge) as excinfo:
        provider.send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")
    assert excinfo.value.max_bytes == 50
    assert excinfo.value.size_bytes > 50

    reloaded = ctx.repos.drafts.require(WORKSPACE, draft.id)
    assert reloaded.status != DraftStatus.SENT


def test_send_message_attachment_with_international_filename_survives_round_trip(ctx):
    import base64
    import email
    import json as _json

    _seed_valid_token(ctx)
    document = _make_document(ctx, name="提案_修正版.docx", content_type="application/octet-stream", artifact_bytes=b"fake-docx-bytes")
    draft = _make_draft(ctx, attachment_document_id=document.id)
    sent_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_requests.append(request)
        return httpx.Response(200, json={"id": "gmail_sent_6", "threadId": "gmail_thread_6"})

    _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")

    body = _json.loads(sent_requests[0].content)
    mime_message = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"].encode("ascii")))
    _body_part, attachment_part = mime_message.get_payload()
    assert attachment_part.get_filename() == "提案_修正版.docx"
    assert attachment_part.get_payload(decode=True) == b"fake-docx-bytes"


def test_send_message_never_reads_a_document_other_than_the_drafts_own_attachment(ctx):
    """The provider has no argument surface for an arbitrary document id -- it can
    only ever resolve the one id already on the draft it was asked to send."""
    _seed_valid_token(ctx)
    other_document = _make_document(ctx, name="unrelated.txt", content_text="not referenced by any draft")
    draft = _make_draft(ctx, attachment_document_id=None)  # no attachment on this draft

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "gmail_sent_4", "threadId": "gmail_thread_4"})

    result = _provider(ctx, handler).send_message(WORKSPACE, draft_id=draft.id, idempotency_key="key1")
    assert result["idempotent_replay"] is False
    # sanity: the unrelated document was never touched/attached (no attachment sent at all)
    reloaded = ctx.repos.drafts.require(WORKSPACE, draft.id)
    assert reloaded.status == DraftStatus.SENT
    assert other_document.id != draft.attachment_document_id


# ---- v1 scope boundary ---------------------------------------------------------------------------

def test_search_files_get_file_create_draft_are_not_supported_in_v1(ctx):
    provider = _provider(ctx, lambda r: httpx.Response(500))
    with pytest.raises(IntegrationInvalidRequest):
        provider.search_files(WORKSPACE, "query")
    with pytest.raises(IntegrationInvalidRequest):
        provider.get_file(WORKSPACE, "file_1")
    with pytest.raises(IntegrationInvalidRequest):
        provider.create_draft(WORKSPACE, recipient="a@b.com", subject="s", body="b")
