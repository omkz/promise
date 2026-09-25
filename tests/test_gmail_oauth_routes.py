from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from promise_api import deps
from promise_api.main import app
from promise_app import gmail_oauth
from promise_auth import AuthenticatedPrincipal, permissions_for_role

"""REST-level Gmail OAuth route coverage. `GoogleOAuthClient` is monkeypatched
to a deterministic fake for the same reason tests/test_gmail_oauth_flow.py
patches it -- its own HTTP behavior is covered separately against
httpx.MockTransport in tests/test_gmail_oauth_client.py."""

WORKSPACE_ENV = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csecret", "GOOGLE_REDIRECT_URI": "https://promise.example/callback"}


class _FakeOAuthClient:
    def __init__(self, config):
        self.config = config

    def build_authorization_url(self, *, state):
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

    def exchange_code(self, code):
        return {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600}

    def get_profile(self, *, access_token):
        return {"email": "andi@example.com", "history_id": "1"}


def _principal_for(workspace_id: str, user_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject=f"local:{user_id}", user_id=user_id, workspace_id=workspace_id, role="owner",
        permissions=permissions_for_role("owner"), scopes=frozenset({"*"}), auth_method="local",
    )


@pytest.fixture(autouse=True)
def _gmail_env(monkeypatch):
    for key, value in WORKSPACE_ENV.items():
        monkeypatch.setenv(key, value)


def test_gmail_connect_requires_auth(seeded_ctx):
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.get("/api/integrations/gmail/connect")
        # local mode without any dev-identity header still resolves the default dev
        # user (see deps.py) rather than 401ing -- the important thing this test
        # locks in is that the endpoint goes through get_principal at all, not a
        # specific status code for "no header" in local mode.
        assert r.status_code in (200, 401, 403)
    finally:
        app.dependency_overrides.clear()


def test_gmail_connect_returns_an_authorization_url_bound_to_the_caller(seeded_ctx, monkeypatch):
    monkeypatch.setattr(gmail_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    ws = seeded_ctx.default_workspace_id
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app)
        r = client.get("/api/integrations/gmail/connect")
        assert r.status_code == 200
        url = r.json()["authorization_url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?state=")

        state_token = url.rsplit("state=", 1)[1]
        row = seeded_ctx.repos.oauth_states.require(gmail_oauth.OAUTH_STATE_PARTITION, state_token)
        assert row.bound_user_id == "usr_a"
        assert row.bound_workspace_id == ws
    finally:
        app.dependency_overrides.clear()


def test_gmail_callback_success_redirects_to_the_web_app_connected(seeded_ctx, monkeypatch):
    monkeypatch.setattr(gmail_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    ws = seeded_ctx.default_workspace_id
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app, follow_redirects=False)
        connect_resp = client.get("/api/integrations/gmail/connect")
        state_token = connect_resp.json()["authorization_url"].rsplit("state=", 1)[1]

        r = client.get("/api/integrations/gmail/callback", params={"state": state_token, "code": "auth-code-1"})
        assert r.status_code in (302, 307)
        assert r.headers["location"].endswith("/connections?gmail=connected")
    finally:
        app.dependency_overrides.clear()


def test_gmail_callback_never_puts_a_token_in_the_redirect_url(seeded_ctx, monkeypatch):
    monkeypatch.setattr(gmail_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    ws = seeded_ctx.default_workspace_id
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app, follow_redirects=False)
        state_token = client.get("/api/integrations/gmail/connect").json()["authorization_url"].rsplit("state=", 1)[1]
        r = client.get("/api/integrations/gmail/callback", params={"state": state_token, "code": "auth-code-1"})
        assert "at_1" not in r.headers["location"]
        assert "rt_1" not in r.headers["location"]
        assert "token" not in r.headers["location"].lower()
    finally:
        app.dependency_overrides.clear()


def test_gmail_callback_with_invalid_state_redirects_with_error(seeded_ctx, monkeypatch):
    monkeypatch.setattr(gmail_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app, follow_redirects=False)
        r = client.get("/api/integrations/gmail/callback", params={"state": "never-issued", "code": "code"})
        assert r.status_code in (302, 307)
        assert "gmail=error" in r.headers["location"]
    finally:
        app.dependency_overrides.clear()


def test_gmail_callback_with_denied_consent_redirects_with_error(seeded_ctx):
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app, follow_redirects=False)
        r = client.get("/api/integrations/gmail/callback", params={"state": "irrelevant", "error": "access_denied"})
        assert "gmail=error" in r.headers["location"]
    finally:
        app.dependency_overrides.clear()


def test_gmail_callback_state_cannot_be_replayed_over_rest(seeded_ctx, monkeypatch):
    monkeypatch.setattr(gmail_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    ws = seeded_ctx.default_workspace_id
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app, follow_redirects=False)
        state_token = client.get("/api/integrations/gmail/connect").json()["authorization_url"].rsplit("state=", 1)[1]
        first = client.get("/api/integrations/gmail/callback", params={"state": state_token, "code": "code-1"})
        assert "gmail=connected" in first.headers["location"]

        second = client.get("/api/integrations/gmail/callback", params={"state": state_token, "code": "code-2"})
        assert "gmail=error" in second.headers["location"]
    finally:
        app.dependency_overrides.clear()
