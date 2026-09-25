from __future__ import annotations

from fastapi.testclient import TestClient
from promise_api import deps
from promise_app import tools

"""Regression: services/api/promise_api/routers/files.py's search_messages/
get_message routes must pass the authenticated principal's own user_id into
tools.search_messages/tools.get_message -- both are user-scoped (Gmail
participates per-caller, see tools.search_messages's own docstring), so a
route that silently dropped user_id would widen every message search/read
back to workspace-wide."""


def _principal_for(workspace_id: str, user_id: str):
    from promise_auth import AuthenticatedPrincipal, permissions_for_role

    return AuthenticatedPrincipal(
        subject=f"local:{user_id}", user_id=user_id, workspace_id=workspace_id, role="owner",
        permissions=permissions_for_role("owner"), scopes=frozenset({"*"}), auth_method="local",
    )


def test_rest_search_messages_passes_the_principals_user_id(seeded_ctx, monkeypatch):
    from promise_api.main import app

    ws = seeded_ctx.default_workspace_id
    captured: dict[str, object] = {}
    original = tools.search_messages

    def spy(ctx, *, workspace_id, user_id, query):
        captured["workspace_id"] = workspace_id
        captured["user_id"] = user_id
        return original(ctx, workspace_id=workspace_id, user_id=user_id, query=query)

    monkeypatch.setattr(tools, "search_messages", spy)
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
    try:
        client = TestClient(app)
        r = client.get("/api/messages", params={"query": ""})
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()

    assert captured["user_id"] == "usr_a"
    assert captured["workspace_id"] == ws


def test_rest_get_message_passes_the_principals_user_id(seeded_ctx, monkeypatch):
    from promise_api.main import app

    ws = seeded_ctx.default_workspace_id
    captured: dict[str, object] = {}
    original = tools.get_message

    def spy(ctx, *, workspace_id, user_id, message_id):
        captured["workspace_id"] = workspace_id
        captured["user_id"] = user_id
        return original(ctx, workspace_id=workspace_id, user_id=user_id, message_id=message_id)

    monkeypatch.setattr(tools, "get_message", spy)
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_b")
    try:
        client = TestClient(app)
        client.get("/api/messages/some_message_id")  # 404 is fine -- only the call args matter here
    finally:
        app.dependency_overrides.clear()

    assert captured["user_id"] == "usr_b"
    assert captured["workspace_id"] == ws


def test_rest_search_messages_is_scoped_per_caller_end_to_end(seeded_ctx):
    """Not just that user_id is passed through, but that two different callers
    hitting the same REST endpoint get independently-scoped results -- same
    guarantee tests/test_gmail_authorization.py locks in at the application
    layer, exercised here through the actual HTTP route."""
    from promise_api.main import app
    from promise_domain.enums import IntegrationStatus
    from promise_domain.models import IntegrationAccount
    from promise_integrations.gmail.mock_provider import MockGmailIntegrationProvider
    from promise_shared.ids import new_id

    ws = seeded_ctx.default_workspace_id
    mock_a = MockGmailIntegrationProvider(seeded_ctx.repos.drafts)
    mock_a.seed_messages([{"id": "m_a", "workspace_id": ws, "subject": "only for A", "content": "a"}])
    mock_b = MockGmailIntegrationProvider(seeded_ctx.repos.drafts)
    mock_b.seed_messages([{"id": "m_b", "workspace_id": ws, "subject": "only for B", "content": "b"}])

    for user_id, mock in (("usr_a", mock_a), ("usr_b", mock_b)):
        seeded_ctx.repos.integration_accounts.save(IntegrationAccount(
            id=new_id("ia"), workspace_id=ws, user_id=user_id, provider="gmail",
            account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref=f"gmail:{user_id}",
        ))
    seeded_ctx.integrations.register_factory("gmail", lambda account: mock_a if account.user_id == "usr_a" else mock_b)

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_a")
        r_a = client.get("/api/messages", params={"query": ""}).json()
        app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, "usr_b")
        r_b = client.get("/api/messages", params={"query": ""}).json()
    finally:
        app.dependency_overrides.clear()

    assert "m_a" in [m["id"] for m in r_a]
    assert "m_b" not in [m["id"] for m in r_a]
    assert "m_b" in [m["id"] for m in r_b]
    assert "m_a" not in [m["id"] for m in r_b]
