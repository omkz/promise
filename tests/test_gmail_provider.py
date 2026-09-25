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
        config=_config(), drafts=ctx.repos.drafts, transport=httpx.MockTransport(handler),
    )


def _seed_valid_token(ctx, *, expires_in: float = 3600) -> None:
    ctx.secret_store.put_secret(SECRET_REF, {
        "access_token": "at_valid", "refresh_token": "rt_1", "expires_at": time.time() + expires_in,
    })


def _make_draft(ctx, *, recipient="andi@example.com", subject="Revised proposal", body="See attached."):
    from promise_domain.models import Draft
    from promise_shared.ids import new_id

    draft = Draft(id=new_id("draft"), workspace_id=WORKSPACE, recipient=recipient, subject=subject, body=body)
    ctx.repos.drafts.save(draft)
    return draft


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


# ---- v1 scope boundary ---------------------------------------------------------------------------

def test_search_files_get_file_create_draft_are_not_supported_in_v1(ctx):
    provider = _provider(ctx, lambda r: httpx.Response(500))
    with pytest.raises(IntegrationInvalidRequest):
        provider.search_files(WORKSPACE, "query")
    with pytest.raises(IntegrationInvalidRequest):
        provider.get_file(WORKSPACE, "file_1")
    with pytest.raises(IntegrationInvalidRequest):
        provider.create_draft(WORKSPACE, recipient="a@b.com", subject="s", body="b")
