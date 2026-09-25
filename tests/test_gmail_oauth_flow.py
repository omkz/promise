from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from promise_app import gmail_oauth, tools
from promise_app.repos import OAUTH_STATE_PARTITION
from promise_domain.models import OAuthState
from promise_shared.errors import IntegrationInvalidRequest

"""Application-level Gmail OAuth flow tests -- state validation/lifecycle and the
full connect -> callback -> IntegrationAccount path. `GoogleOAuthClient` itself
is monkeypatched to a deterministic fake (its own HTTP behavior is covered
separately in tests/test_gmail_oauth_client.py against httpx.MockTransport);
this file is about promise_app.gmail_oauth's own orchestration: state
single-use/expiry/binding, account creation, and secure token storage."""

WORKSPACE = "ws_1"
USER_A = "usr_a"
USER_B = "usr_b"


class _FakeOAuthClient:
    def __init__(self, config, *, email="andi@example.com", refresh_token="rt_1"):
        self.config = config
        self._email = email
        self._refresh_token = refresh_token

    def build_authorization_url(self, *, state):
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

    def exchange_code(self, code):
        return {"access_token": f"at_for_{code}", "refresh_token": self._refresh_token, "expires_at": time.time() + 3600}

    def get_profile(self, *, access_token):
        return {"email": self._email, "history_id": "hist_1"}


@pytest.fixture(autouse=True)
def _gmail_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://promise.example/callback")


def _patch_client(monkeypatch, **kwargs):
    monkeypatch.setattr(gmail_oauth, "GoogleOAuthClient", lambda config: _FakeOAuthClient(config, **kwargs))


def test_start_gmail_oauth_returns_a_state_bound_authorization_url(ctx):
    from urllib.parse import parse_qs, urlparse

    url = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    state_token = parse_qs(urlparse(url).query)["state"][0]

    row = ctx.repos.oauth_states.require(OAUTH_STATE_PARTITION, state_token)
    assert row.bound_workspace_id == WORKSPACE
    assert row.bound_user_id == USER_A
    assert row.consumed_at is None


def test_start_gmail_oauth_state_is_random_and_not_predictable(ctx):
    url_a = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    url_b = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    assert url_a != url_b


def test_callback_with_unknown_state_is_rejected(ctx, monkeypatch):
    _patch_client(monkeypatch)
    with pytest.raises(IntegrationInvalidRequest):
        gmail_oauth.handle_gmail_oauth_callback(ctx, state_token="never-issued", code="code")


def test_callback_with_expired_state_is_rejected(ctx, monkeypatch):
    _patch_client(monkeypatch)
    expired = OAuthState(
        id="tok_expired", workspace_id=OAUTH_STATE_PARTITION, provider="gmail",
        bound_workspace_id=WORKSPACE, bound_user_id=USER_A,
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    ctx.repos.oauth_states.save(expired)

    with pytest.raises(IntegrationInvalidRequest):
        gmail_oauth.handle_gmail_oauth_callback(ctx, state_token="tok_expired", code="code")


def test_callback_state_is_single_use(ctx, monkeypatch):
    _patch_client(monkeypatch)
    url = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    state_token = url.rsplit("state=", 1)[1]

    gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=state_token, code="code-1")
    with pytest.raises(IntegrationInvalidRequest):
        gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=state_token, code="code-2")


def test_callback_success_creates_a_connected_account_with_tokens_only_in_the_secret_store(ctx, monkeypatch):
    _patch_client(monkeypatch, email="andi@example.com")
    url = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    state_token = url.rsplit("state=", 1)[1]

    account = gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=state_token, code="auth-code")

    assert account.provider == "gmail"
    assert account.workspace_id == WORKSPACE
    assert account.user_id == USER_A
    assert account.account_identifier == "andi@example.com"
    assert account.status.value == "connected"
    assert account.secret_ref is not None

    # tokens live only in the secret store, never on the account record itself
    dumped = account.model_dump(mode="json")
    assert "refresh_token" not in dumped
    assert "access_token" not in dumped
    secret = ctx.secret_store.get_secret(account.secret_ref)
    assert secret["refresh_token"] == "rt_1"
    assert secret["access_token"] == "at_for_auth-code"


def test_callback_binds_identity_from_the_validated_state_not_a_query_parameter(ctx, monkeypatch):
    """The callback signature takes no workspace_id/user_id argument at all --
    this test locks in that the *only* way identity reaches
    handle_gmail_oauth_callback is through the state row created by
    start_gmail_oauth for a specific principal."""
    _patch_client(monkeypatch)
    url = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_B)
    state_token = url.rsplit("state=", 1)[1]

    account = gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=state_token, code="code")
    assert account.user_id == USER_B
    assert account.workspace_id == WORKSPACE


def test_reconnect_preserves_refresh_token_when_google_does_not_repeat_it(ctx, monkeypatch):
    _patch_client(monkeypatch, refresh_token="rt_original")
    url1 = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    account = gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=url1.rsplit("state=", 1)[1], code="code-1")

    # a reconnect where Google's exchange doesn't include a fresh refresh_token
    _patch_client(monkeypatch, refresh_token=None)
    url2 = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    account2 = gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=url2.rsplit("state=", 1)[1], code="code-2")

    assert account2.id == account.id  # same account, updated in place
    secret = ctx.secret_store.get_secret(account2.secret_ref)
    assert secret["refresh_token"] == "rt_original"  # never silently dropped


def test_disconnect_after_gmail_connect_marks_the_account_disconnected(ctx, monkeypatch):
    _patch_client(monkeypatch)
    url = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    account = gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=url.rsplit("state=", 1)[1], code="code")

    disconnected = tools.disconnect_integration_account(ctx, workspace_id=WORKSPACE, user_id=USER_A, account_id=account.id)
    assert disconnected.status.value == "disconnected"
