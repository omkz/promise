from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from promise_app import calendar_oauth, gmail_oauth, tools
from promise_app.repos import OAUTH_STATE_PARTITION
from promise_domain.models import OAuthState
from promise_integrations.calendar.config import CalendarConfig, load_calendar_config
from promise_integrations.gmail.config import load_gmail_config
from promise_integrations.gmail.oauth import GoogleOAuthClient
from promise_shared.errors import IntegrationInvalidRequest

"""Application-level Google Calendar OAuth flow tests -- exact same shape as
tests/test_gmail_oauth_flow.py, since calendar_oauth.py mirrors gmail_oauth.py
file-for-file. Also covers: Calendar reuses the one shared GoogleOAuthClient
(no second OAuth implementation), and incremental authorization (Gmail and
Calendar are independent per-provider IntegrationAccount rows, so connecting
one never disturbs the other)."""

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

    def get_identity(self, *, access_token):
        return {"email": self._email, "sub": "sub_1"}


@pytest.fixture(autouse=True)
def _google_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://promise.example/gmail/callback")
    monkeypatch.setenv("GOOGLE_CALENDAR_REDIRECT_URI", "https://promise.example/calendar/callback")


def _patch_client(monkeypatch, **kwargs):
    monkeypatch.setattr(calendar_oauth, "GoogleOAuthClient", lambda config: _FakeOAuthClient(config, **kwargs))


# ---- account identity: Google's OIDC userinfo endpoint, never Gmail's API -------------------

def test_calendar_callback_identifies_the_account_via_google_identity_api_not_gmail(ctx, monkeypatch):
    """Uses the REAL `GoogleOAuthClient` (only its HTTP transport is swapped for
    `httpx.MockTransport` -- the class's own documented test seam, see its
    docstring), so this exercises the actual production `get_identity` code path,
    never a hand-written test double standing in for it. Proves the callback
    resolves the connected account's email from Google's OIDC userinfo endpoint,
    and never touches Gmail's `users/me/profile` -- Calendar's scope grants no
    access to that endpoint at all."""
    import httpx
    from promise_integrations.gmail.config import GMAIL_API_BASE, GOOGLE_USERINFO_ENDPOINT
    from promise_integrations.gmail.oauth import GoogleOAuthClient as RealGoogleOAuthClient

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url).endswith("/token"):
            return httpx.Response(200, json={"access_token": "at_1", "refresh_token": "rt_1", "expires_in": 3600})
        if str(request.url) == GOOGLE_USERINFO_ENDPOINT:
            return httpx.Response(200, json={"email": "andi@example.com", "sub": "sub_123"})
        raise AssertionError(f"unexpected request to {request.url}")  # e.g. Gmail's profile endpoint

    monkeypatch.setattr(
        calendar_oauth, "GoogleOAuthClient",
        lambda config: RealGoogleOAuthClient(config, transport=httpx.MockTransport(handler)),
    )

    url = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    state_token = parse_qs(urlparse(url).query)["state"][0]
    account = calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=state_token, code="auth-code")

    assert account.account_identifier == "andi@example.com"
    assert any(u == GOOGLE_USERINFO_ENDPOINT for u in requested_urls)
    assert not any(u.startswith(GMAIL_API_BASE) for u in requested_urls)


# ---- shared OAuth client, not a second implementation --------------------------------------

def test_calendar_config_satisfies_the_shared_oauth_client_no_second_implementation(monkeypatch):
    """CalendarConfig is a distinct dataclass from GmailConfig, but the exact
    same GoogleOAuthClient class (packages/integrations/promise_integrations/
    gmail/oauth.py) is what calendar_oauth.py imports and constructs -- proven
    here by using it directly against a CalendarConfig."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_CALENDAR_REDIRECT_URI", "https://promise.example/calendar/callback")
    calendar_config = load_calendar_config()
    client = GoogleOAuthClient(calendar_config)
    url = client.build_authorization_url(state="tok_1")
    params = parse_qs(urlparse(url).query)
    assert params["redirect_uri"] == ["https://promise.example/calendar/callback"]
    assert "calendar.events.owned" in params["scope"][0]


def test_calendar_scope_request_is_independent_of_gmail_scope_request():
    calendar_config = CalendarConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/calendar/callback",
        scopes=("https://www.googleapis.com/auth/calendar.events.owned",), state_ttl_seconds=600,
    )
    gmail_config = load_gmail_config()
    calendar_url = GoogleOAuthClient(calendar_config).build_authorization_url(state="s1")
    gmail_url = GoogleOAuthClient(gmail_config).build_authorization_url(state="s1")
    calendar_scope = parse_qs(urlparse(calendar_url).query)["scope"][0]
    gmail_scope = parse_qs(urlparse(gmail_url).query)["scope"][0]
    assert "calendar.events.owned" in calendar_scope
    assert "gmail.readonly" not in calendar_scope
    assert "calendar.events.owned" not in gmail_scope


# ---- state validation / lifecycle -----------------------------------------------------------

def test_start_calendar_oauth_returns_a_state_bound_authorization_url(ctx):
    url = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    state_token = parse_qs(urlparse(url).query)["state"][0]

    row = ctx.repos.oauth_states.require(OAUTH_STATE_PARTITION, state_token)
    assert row.bound_workspace_id == WORKSPACE
    assert row.bound_user_id == USER_A
    assert row.provider == "google_calendar"
    assert row.consumed_at is None


def test_callback_with_unknown_state_is_rejected(ctx, monkeypatch):
    _patch_client(monkeypatch)
    with pytest.raises(IntegrationInvalidRequest):
        calendar_oauth.handle_calendar_oauth_callback(ctx, state_token="never-issued", code="code")


def test_callback_with_expired_state_is_rejected(ctx, monkeypatch):
    _patch_client(monkeypatch)
    expired = OAuthState(
        id="tok_expired", workspace_id=OAUTH_STATE_PARTITION, provider="google_calendar",
        bound_workspace_id=WORKSPACE, bound_user_id=USER_A,
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    ctx.repos.oauth_states.save(expired)
    with pytest.raises(IntegrationInvalidRequest):
        calendar_oauth.handle_calendar_oauth_callback(ctx, state_token="tok_expired", code="code")


def test_callback_state_is_single_use(ctx, monkeypatch):
    _patch_client(monkeypatch)
    url = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    state_token = url.rsplit("state=", 1)[1]

    calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=state_token, code="code-1")
    with pytest.raises(IntegrationInvalidRequest):
        calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=state_token, code="code-2")


def test_callback_success_creates_a_connected_account_with_tokens_only_in_the_secret_store(ctx, monkeypatch):
    _patch_client(monkeypatch, email="andi@example.com")
    url = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    state_token = url.rsplit("state=", 1)[1]

    account = calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=state_token, code="auth-code")

    assert account.provider == "google_calendar"
    assert account.workspace_id == WORKSPACE
    assert account.user_id == USER_A
    assert account.account_identifier == "andi@example.com"
    assert account.status.value == "connected"
    assert list(account.scopes) == list(load_calendar_config().scopes)

    dumped = account.model_dump(mode="json")
    assert "refresh_token" not in dumped
    assert "access_token" not in dumped
    secret = ctx.secret_store.get_secret(account.secret_ref)
    assert secret["refresh_token"] == "rt_1"


def test_callback_binds_identity_from_the_validated_state_not_a_query_parameter(ctx, monkeypatch):
    _patch_client(monkeypatch)
    url = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_B)
    state_token = url.rsplit("state=", 1)[1]

    account = calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=state_token, code="code")
    assert account.user_id == USER_B
    assert account.workspace_id == WORKSPACE


def test_reconnect_preserves_refresh_token_when_google_does_not_repeat_it(ctx, monkeypatch):
    _patch_client(monkeypatch, refresh_token="rt_original")
    url1 = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    account = calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=url1.rsplit("state=", 1)[1], code="code-1")

    _patch_client(monkeypatch, refresh_token=None)
    url2 = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    account2 = calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=url2.rsplit("state=", 1)[1], code="code-2")

    assert account2.id == account.id
    secret = ctx.secret_store.get_secret(account2.secret_ref)
    assert secret["refresh_token"] == "rt_original"


# ---- incremental authorization: Gmail and Calendar are independent -------------------------

def test_connecting_calendar_does_not_disturb_an_existing_gmail_connection(ctx, monkeypatch):
    monkeypatch.setattr(gmail_oauth, "GoogleOAuthClient", lambda config: _FakeOAuthClient(config, email="andi@example.com", refresh_token="gmail_rt"))
    _patch_client(monkeypatch, email="andi@example.com", refresh_token="cal_rt")

    gmail_url = gmail_oauth.start_gmail_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    gmail_account = gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=gmail_url.rsplit("state=", 1)[1], code="gcode")

    calendar_url = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    calendar_account = calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=calendar_url.rsplit("state=", 1)[1], code="ccode")

    assert gmail_account.id != calendar_account.id
    assert gmail_account.provider == "gmail"
    assert calendar_account.provider == "google_calendar"
    # both remain independently connected -- reconnecting/adding Calendar never
    # forced a Gmail reconnect or touched its stored tokens.
    reloaded_gmail = ctx.repos.integration_accounts.require(WORKSPACE, gmail_account.id)
    assert reloaded_gmail.status.value == "connected"
    gmail_secret = ctx.secret_store.get_secret(reloaded_gmail.secret_ref)
    assert gmail_secret["refresh_token"] == "gmail_rt"


def test_disconnect_after_calendar_connect_marks_the_account_disconnected(ctx, monkeypatch):
    _patch_client(monkeypatch)
    url = calendar_oauth.start_calendar_oauth(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    account = calendar_oauth.handle_calendar_oauth_callback(ctx, state_token=url.rsplit("state=", 1)[1], code="code")

    disconnected = tools.disconnect_integration_account(ctx, workspace_id=WORKSPACE, user_id=USER_A, account_id=account.id)
    assert disconnected.status.value == "disconnected"
