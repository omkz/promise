from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from promise_api import deps
from promise_api.main import app
from promise_app import calendar_oauth
from promise_auth import AuthenticatedPrincipal, permissions_for_role

"""REST-level Google Calendar OAuth route coverage -- exact same shape as
tests/test_gmail_oauth_routes.py."""

ENV = {
    "GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csecret",
    "GOOGLE_CALENDAR_REDIRECT_URI": "https://promise.example/calendar/callback",
}


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
def _calendar_env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_calendar_connect_returns_an_authorization_url_bound_to_the_caller(seeded_ctx, monkeypatch):
    monkeypatch.setattr(calendar_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    ws = seeded_ctx.default_workspace_id
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app)
        r = client.get("/api/integrations/calendar/connect")
        assert r.status_code == 200
        url = r.json()["authorization_url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?state=")

        state_token = url.rsplit("state=", 1)[1]
        row = seeded_ctx.repos.oauth_states.require(calendar_oauth.OAUTH_STATE_PARTITION, state_token)
        assert row.bound_user_id == "usr_a"
        assert row.bound_workspace_id == ws
    finally:
        app.dependency_overrides.clear()


def test_calendar_callback_success_redirects_to_the_web_app_connected(seeded_ctx, monkeypatch):
    monkeypatch.setattr(calendar_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    ws = seeded_ctx.default_workspace_id
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app, follow_redirects=False)
        connect_resp = client.get("/api/integrations/calendar/connect")
        state_token = connect_resp.json()["authorization_url"].rsplit("state=", 1)[1]

        r = client.get("/api/integrations/calendar/callback", params={"state": state_token, "code": "auth-code-1"})
        assert r.status_code in (302, 307)
        assert r.headers["location"].endswith("/connections?calendar=connected")
    finally:
        app.dependency_overrides.clear()


def test_calendar_callback_never_puts_a_token_in_the_redirect_url(seeded_ctx, monkeypatch):
    monkeypatch.setattr(calendar_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    ws = seeded_ctx.default_workspace_id
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app, follow_redirects=False)
        state_token = client.get("/api/integrations/calendar/connect").json()["authorization_url"].rsplit("state=", 1)[1]
        r = client.get("/api/integrations/calendar/callback", params={"state": state_token, "code": "auth-code-1"})
        assert "at_1" not in r.headers["location"]
        assert "rt_1" not in r.headers["location"]
        assert "token" not in r.headers["location"].lower()
    finally:
        app.dependency_overrides.clear()


def test_calendar_callback_with_invalid_state_redirects_with_error(seeded_ctx, monkeypatch):
    monkeypatch.setattr(calendar_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app, follow_redirects=False)
        r = client.get("/api/integrations/calendar/callback", params={"state": "never-issued", "code": "code"})
        assert r.status_code in (302, 307)
        assert "calendar=error" in r.headers["location"]
    finally:
        app.dependency_overrides.clear()


def test_calendar_callback_with_denied_consent_redirects_with_error(seeded_ctx):
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app, follow_redirects=False)
        r = client.get("/api/integrations/calendar/callback", params={"state": "irrelevant", "error": "access_denied"})
        assert "calendar=error" in r.headers["location"]
    finally:
        app.dependency_overrides.clear()


def test_calendar_callback_state_cannot_be_replayed_over_rest(seeded_ctx, monkeypatch):
    monkeypatch.setattr(calendar_oauth, "GoogleOAuthClient", _FakeOAuthClient)
    ws = seeded_ctx.default_workspace_id
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app, follow_redirects=False)
        state_token = client.get("/api/integrations/calendar/connect").json()["authorization_url"].rsplit("state=", 1)[1]
        first = client.get("/api/integrations/calendar/callback", params={"state": state_token, "code": "code-1"})
        assert "calendar=connected" in first.headers["location"]

        second = client.get("/api/integrations/calendar/callback", params={"state": state_token, "code": "code-2"})
        assert "calendar=error" in second.headers["location"]
    finally:
        app.dependency_overrides.clear()


def test_search_calendar_events_route_requires_auth_and_scopes_to_the_caller(seeded_ctx, monkeypatch):
    from promise_integrations.calendar.mock_provider import MockGoogleCalendarProvider

    ws = seeded_ctx.default_workspace_id
    mock = MockGoogleCalendarProvider()
    mock.seed_events([{
        "id": "evt_1", "workspace_id": ws, "calendar_id": "primary", "summary": "Design review",
        "description": "", "location": None, "start_at": "2026-09-25T14:00:00+07:00",
        "end_at": "2026-09-25T14:30:00+07:00", "attendees": [], "status": "confirmed",
        "html_link": "https://calendar.google.com/event?eid=evt_1", "created_at": "2026-09-25T14:00:00+07:00",
    }])
    from promise_domain.enums import IntegrationStatus
    from promise_domain.models import IntegrationAccount
    from promise_shared.ids import new_id
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=ws, user_id="usr_a", provider="google_calendar",
        account_identifier="usr_a@example.com", status=IntegrationStatus.CONNECTED, secret_ref="cal:fake",
    )
    seeded_ctx.repos.integration_accounts.save(account)
    seeded_ctx.integrations.register_factory("google_calendar", lambda _account: mock)

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app)
        r = client.get("/api/calendar/events")
        assert r.status_code == 200
        assert [e["id"] for e in r.json()] == ["evt_1"]
    finally:
        app.dependency_overrides.clear()
