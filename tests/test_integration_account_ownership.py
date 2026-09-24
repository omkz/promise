from __future__ import annotations

import pytest
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_domain.models import Workspace
from promise_shared.errors import WorkspaceAccessError
from promise_shared.ids import new_id

"""Regression tests: IntegrationAccount is a user-owned resource (its own
`user_id` field, same shape as Commitment) -- workspace membership alone must
never be enough to list or disconnect another user's connected account, even
within the same workspace. See `promise_app.tools.list_integration_accounts`/
`_require_owned_integration_account`."""

OWNER = "usr_owner"
OTHER = "usr_other"


def _connect_as(ctx, user_id: str, provider: str = "gmail"):
    return tools.connect_integration_account(
        ctx, workspace_id=ctx.default_workspace_id, user_id=user_id, provider=provider, account_identifier=f"{user_id}@example.com"
    )


def test_list_integration_accounts_is_scoped_per_user(ctx):
    owner_account = _connect_as(ctx, OWNER)
    _connect_as(ctx, OTHER)

    rows = tools.list_integration_accounts(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER)
    assert [a.id for a in rows] == [owner_account.id]


def test_disconnect_integration_account_cross_user_is_rejected(ctx):
    account = _connect_as(ctx, OWNER)
    with pytest.raises(WorkspaceAccessError):
        tools.disconnect_integration_account(ctx, workspace_id=ctx.default_workspace_id, user_id=OTHER, account_id=account.id)

    # never actually mutated
    unchanged = ctx.repos.integration_accounts.require(ctx.default_workspace_id, account.id)
    assert unchanged.status.value != "disconnected"


def test_owner_can_disconnect_their_own_integration_account(ctx):
    account = _connect_as(ctx, OWNER)
    disconnected = tools.disconnect_integration_account(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, account_id=account.id)
    assert disconnected.status.value == "disconnected"


def test_integration_accounts_do_not_leak_across_workspaces(ctx: AppContext):
    other_ws = Workspace(id=new_id("ws"), name="Other Co", slug="other-co")
    ctx.repos.workspaces.save(other_ws)

    tools.connect_integration_account(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, provider="gmail", account_identifier="a@example.com")
    tools.connect_integration_account(ctx, workspace_id=other_ws.id, user_id=OWNER, provider="gmail", account_identifier="b@example.com")

    default_rows = tools.list_integration_accounts(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER)
    other_rows = tools.list_integration_accounts(ctx, workspace_id=other_ws.id, user_id=OWNER)
    assert len(default_rows) == 1
    assert len(other_rows) == 1
    assert default_rows[0].id != other_rows[0].id


# ---- REST level: cross-user is 403, listing is scoped -----------------------------------------------

def _principal_for(workspace_id: str, user_id: str):
    from promise_auth import AuthenticatedPrincipal, permissions_for_role

    return AuthenticatedPrincipal(
        subject=f"local:{user_id}", user_id=user_id, workspace_id=workspace_id, role="owner",
        permissions=permissions_for_role("owner"), scopes=frozenset({"*"}), auth_method="local",
    )


def test_rest_list_integration_accounts_is_scoped_per_user(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    ws = seeded_ctx.default_workspace_id
    _connect_as(seeded_ctx, OWNER)
    _connect_as(seeded_ctx, OTHER)

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, OWNER)
        client = TestClient(app)
        r = client.get("/api/integrations")
        assert r.status_code == 200
        assert len(r.json()) == 1
    finally:
        app.dependency_overrides.clear()


def test_rest_disconnect_integration_account_cross_user_is_403(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    ws = seeded_ctx.default_workspace_id
    account = _connect_as(seeded_ctx, OWNER)

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        app.dependency_overrides[deps.get_principal] = lambda: _principal_for(ws, OTHER)
        client = TestClient(app)
        r = client.post(f"/api/integrations/{account.id}/disconnect")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()
