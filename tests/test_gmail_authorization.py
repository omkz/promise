from __future__ import annotations

import pytest
from promise_app import tools
from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount, Workspace
from promise_integrations.gmail.mock_provider import MockGmailIntegrationProvider
from promise_shared.errors import WorkspaceAccessError
from promise_shared.ids import new_id

"""Gmail-specific ownership/isolation regressions -- IntegrationAccount's generic
ownership behavior (list/disconnect scoped by workspace_id + user_id) is already
covered by tests/test_integration_account_ownership.py; this file locks in the
Gmail-specific consequence: a resolved GmailIntegrationProvider is always the
*calling* user's own mailbox, never another user's, even within the same
workspace."""

WORKSPACE = "ws_1"
USER_A = "usr_a"
USER_B = "usr_b"


def _connect_gmail(ctx, *, user_id: str, workspace_id: str = WORKSPACE) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="gmail",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref=f"gmail:{user_id}",
    )
    ctx.repos.integration_accounts.save(account)
    return account


def test_user_a_cannot_resolve_user_b_gmail_connection(ctx):
    mock_a = MockGmailIntegrationProvider(ctx.repos.drafts)
    mock_b = MockGmailIntegrationProvider(ctx.repos.drafts)
    _connect_gmail(ctx, user_id=USER_A)
    _connect_gmail(ctx, user_id=USER_B)
    ctx.integrations.register_factory("gmail", lambda account: mock_a if account.user_id == USER_A else mock_b)

    resolved_for_a = ctx.integrations.resolve_for_user(WORKSPACE, USER_A, provider_name="gmail")
    resolved_for_b = ctx.integrations.resolve_for_user(WORKSPACE, USER_B, provider_name="gmail")

    assert resolved_for_a is mock_a
    assert resolved_for_b is mock_b
    assert resolved_for_a is not resolved_for_b


def test_user_with_no_connection_resolves_to_none_even_if_someone_else_in_the_workspace_is_connected(ctx):
    _connect_gmail(ctx, user_id=USER_A)
    ctx.integrations.register_factory("gmail", lambda account: MockGmailIntegrationProvider(ctx.repos.drafts))

    assert ctx.integrations.resolve_for_user(WORKSPACE, USER_B, provider_name="gmail") is None


def test_gmail_account_list_is_scoped_per_user(ctx):
    a_account = _connect_gmail(ctx, user_id=USER_A)
    _connect_gmail(ctx, user_id=USER_B)

    rows = tools.list_integration_accounts(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    assert [r.id for r in rows] == [a_account.id]


def test_user_b_cannot_disconnect_user_a_gmail_account(ctx):
    account = _connect_gmail(ctx, user_id=USER_A)
    with pytest.raises(WorkspaceAccessError):
        tools.disconnect_integration_account(ctx, workspace_id=WORKSPACE, user_id=USER_B, account_id=account.id)

    unchanged = ctx.repos.integration_accounts.require(WORKSPACE, account.id)
    assert unchanged.status == IntegrationStatus.CONNECTED


def test_gmail_connections_do_not_leak_across_workspaces(ctx):
    other_ws = Workspace(id=new_id("ws"), name="Other Co", slug="other-co")
    ctx.repos.workspaces.save(other_ws)
    _connect_gmail(ctx, user_id=USER_A, workspace_id=WORKSPACE)
    _connect_gmail(ctx, user_id=USER_A, workspace_id=other_ws.id)
    ctx.integrations.register_factory("gmail", lambda account: MockGmailIntegrationProvider(ctx.repos.drafts))

    default_rows = tools.list_integration_accounts(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    other_rows = tools.list_integration_accounts(ctx, workspace_id=other_ws.id, user_id=USER_A)
    assert len(default_rows) == 1 and len(other_rows) == 1
    assert default_rows[0].id != other_rows[0].id

    resolved_other = ctx.integrations.resolve_for_user(other_ws.id, USER_A, provider_name="gmail")
    resolved_default = ctx.integrations.resolve_for_user(WORKSPACE, USER_A, provider_name="gmail")
    assert resolved_other is not None and resolved_default is not None
