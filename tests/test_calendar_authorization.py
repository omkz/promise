from __future__ import annotations

import pytest
from promise_app import tools
from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount, Workspace
from promise_integrations.calendar.mock_provider import MockGoogleCalendarProvider
from promise_shared.errors import WorkspaceAccessError
from promise_shared.ids import new_id

"""Calendar-specific ownership/isolation regressions -- mirrors
tests/test_gmail_authorization.py exactly. IntegrationAccount's generic
ownership behavior is covered by tests/test_integration_account_ownership.py;
this file locks in the Calendar-specific consequence: a resolved
GoogleCalendarIntegrationProvider (or its mock) is always the *calling*
user's own calendar, never another user's."""

WORKSPACE = "ws_1"
USER_A = "usr_a"
USER_B = "usr_b"


def _connect_calendar(ctx, *, user_id: str, workspace_id: str = WORKSPACE) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="google_calendar",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref=f"cal:{user_id}",
    )
    ctx.repos.integration_accounts.save(account)
    return account


def test_user_a_cannot_resolve_user_b_calendar_connection(ctx):
    mock_a = MockGoogleCalendarProvider()
    mock_b = MockGoogleCalendarProvider()
    _connect_calendar(ctx, user_id=USER_A)
    _connect_calendar(ctx, user_id=USER_B)
    ctx.integrations.register_factory("google_calendar", lambda account: mock_a if account.user_id == USER_A else mock_b)

    resolved_for_a = ctx.integrations.resolve_for_user(WORKSPACE, USER_A, provider_name="google_calendar")
    resolved_for_b = ctx.integrations.resolve_for_user(WORKSPACE, USER_B, provider_name="google_calendar")

    assert resolved_for_a is mock_a
    assert resolved_for_b is mock_b
    assert resolved_for_a is not resolved_for_b


def test_user_with_no_connection_resolves_to_none_even_if_someone_else_in_the_workspace_is_connected(ctx):
    _connect_calendar(ctx, user_id=USER_A)
    ctx.integrations.register_factory("google_calendar", lambda account: MockGoogleCalendarProvider())

    assert ctx.integrations.resolve_for_user(WORKSPACE, USER_B, provider_name="google_calendar") is None


def test_gmail_only_account_does_not_pretend_to_have_calendar_access(ctx):
    """A user with a connected Gmail account but no Calendar account must
    resolve to None for google_calendar -- 'has Gmail' is never treated as
    'has Calendar permission' (each provider/scope is checked independently)."""
    gmail_account = IntegrationAccount(
        id=new_id("ia"), workspace_id=WORKSPACE, user_id=USER_A, provider="gmail",
        account_identifier=f"{USER_A}@example.com", status=IntegrationStatus.CONNECTED, secret_ref="gmail:fake",
    )
    ctx.repos.integration_accounts.save(gmail_account)
    ctx.integrations.register_factory("google_calendar", lambda account: MockGoogleCalendarProvider())

    assert ctx.integrations.resolve_for_user(WORKSPACE, USER_A, provider_name="google_calendar") is None


def test_calendar_account_list_is_scoped_per_user(ctx):
    a_account = _connect_calendar(ctx, user_id=USER_A)
    _connect_calendar(ctx, user_id=USER_B)

    rows = tools.list_integration_accounts(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    assert [r.id for r in rows] == [a_account.id]


def test_user_b_cannot_disconnect_user_a_calendar_account(ctx):
    account = _connect_calendar(ctx, user_id=USER_A)
    with pytest.raises(WorkspaceAccessError):
        tools.disconnect_integration_account(ctx, workspace_id=WORKSPACE, user_id=USER_B, account_id=account.id)

    unchanged = ctx.repos.integration_accounts.require(WORKSPACE, account.id)
    assert unchanged.status == IntegrationStatus.CONNECTED


def test_calendar_connections_do_not_leak_across_workspaces(ctx):
    other_ws = Workspace(id=new_id("ws"), name="Other Co", slug="other-co")
    ctx.repos.workspaces.save(other_ws)
    _connect_calendar(ctx, user_id=USER_A, workspace_id=WORKSPACE)
    _connect_calendar(ctx, user_id=USER_A, workspace_id=other_ws.id)
    ctx.integrations.register_factory("google_calendar", lambda account: MockGoogleCalendarProvider())

    default_rows = tools.list_integration_accounts(ctx, workspace_id=WORKSPACE, user_id=USER_A)
    other_rows = tools.list_integration_accounts(ctx, workspace_id=other_ws.id, user_id=USER_A)
    assert len(default_rows) == 1 and len(other_rows) == 1
    assert default_rows[0].id != other_rows[0].id

    resolved_other = ctx.integrations.resolve_for_user(other_ws.id, USER_A, provider_name="google_calendar")
    resolved_default = ctx.integrations.resolve_for_user(WORKSPACE, USER_A, provider_name="google_calendar")
    assert resolved_other is not None and resolved_default is not None


def test_user_a_cannot_create_an_event_using_user_bs_calendar_account(seeded_ctx):
    """End-to-end: even though execute_approved_action resolves whichever
    Calendar account belongs to the *executing* actor, actor identity itself
    is already ownership-checked by tools.execute_approved_action
    (_require_owned_action) before execution.execute_action ever runs -- user
    B can't execute an action that belongs to user A's commitment at all."""
    ctx = seeded_ctx
    ws = ctx.default_workspace_id
    mock_a = MockGoogleCalendarProvider()
    _connect_calendar(ctx, user_id=ctx.default_user_id, workspace_id=ws)
    ctx.integrations.register_factory("google_calendar", lambda account: mock_a)

    commitment = tools.create_commitment(ctx, workspace_id=ws, user_id=ctx.default_user_id, text="I need to meet Andi Friday at 2.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=ctx.default_user_id, commitment_id=commitment.id)
    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=ctx.default_user_id)

    with pytest.raises(WorkspaceAccessError):
        tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=USER_B)
    assert mock_a.created_event_ids == []
