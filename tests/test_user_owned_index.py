from __future__ import annotations

import pytest
from promise_app import tools
from promise_shared.errors import WorkspaceAccessError

"""Regression tests for the `UserOwnedIndex` GSI access path
(`Repository.list_user_owned`, `EntityStore.query_index`) that
`tools.search_commitments`/`tools.list_integration_accounts` now go through
instead of `Repository.list(workspace_id, user_id=...)` (workspace-wide read
+ Python filter). See `packages/shared/promise_shared/store/index_keys.py`
and the root README's "DynamoDB access patterns" section."""

USER_A = "usr_a"
USER_B = "usr_b"


def _commitment(ctx, user_id: str, text: str):
    return tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=user_id, text=text)["commitment"]


def _integration_account(ctx, user_id: str, provider: str = "gmail"):
    return tools.connect_integration_account(
        ctx, workspace_id=ctx.default_workspace_id, user_id=user_id, provider=provider, account_identifier=f"{user_id}@example.com"
    )


# ---- commitments: the exact regression named in the task spec --------------------------------------

def test_user_a_and_user_b_each_see_only_their_own_commitments(ctx):
    a1 = _commitment(ctx, USER_A, "I'll send Andi the revised proposal tomorrow morning.")
    b1 = _commitment(ctx, USER_B, "I'll call Sam today.")

    a_rows = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A)
    b_rows = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_B)

    assert [c.id for c in a_rows] == [a1.id]
    assert [c.id for c in b_rows] == [b1.id]


def test_search_commitments_status_filter_still_works_over_the_indexed_result(ctx):
    open_one = _commitment(ctx, USER_A, "I'll send Andi the revised proposal tomorrow morning.")
    tools.cancel_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A, commitment_id=(
        _commitment(ctx, USER_A, "I'll call Sam today.").id
    ))

    rows = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A, status="open")
    assert [c.id for c in rows] == [open_one.id]


def test_search_commitments_ordering_is_still_by_due_at(ctx):
    tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A, text="I'll call Sam today.")
    first = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A, text="I'll send Andi the revised proposal tomorrow morning."
    )["commitment"]
    tools.update_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A, commitment_id=first.id, due_at="2020-01-01T00:00:00+00:00")

    rows = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A)
    assert rows[0].id == first.id


# ---- integration accounts ---------------------------------------------------------------------------

def test_user_a_and_user_b_each_see_only_their_own_integration_accounts(ctx):
    a_account = _integration_account(ctx, USER_A)
    b_account = _integration_account(ctx, USER_B)

    a_rows = tools.list_integration_accounts(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A)
    b_rows = tools.list_integration_accounts(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_B)

    assert [a.id for a in a_rows] == [a_account.id]
    assert [a.id for a in b_rows] == [b_account.id]


def test_list_user_owned_respects_limit(ctx):
    _integration_account(ctx, USER_A, provider="gmail")
    _integration_account(ctx, USER_A, provider="drive")

    rows = ctx.repos.integration_accounts.list_user_owned(ctx.default_workspace_id, USER_A, limit=1)
    assert len(rows) == 1


def test_list_user_owned_ordering_is_chronological(ctx):
    first = _integration_account(ctx, USER_A, provider="gmail")
    second = _integration_account(ctx, USER_A, provider="drive")

    rows = tools.list_integration_accounts(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A)
    assert [a.id for a in rows] == [first.id, second.id]


# ---- cross-workspace isolation is preserved through the indexed path ---------------------------------

def test_indexed_list_still_respects_workspace_isolation(ctx):
    from promise_domain.models import Workspace
    from promise_shared.ids import new_id

    other_ws = Workspace(id=new_id("ws"), name="Other Co", slug="other-co")
    ctx.repos.workspaces.save(other_ws)

    tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A, text="I'll call Sam today.")
    tools.create_commitment(ctx, workspace_id=other_ws.id, user_id=USER_A, text="I'll email Priya tomorrow.")

    default_rows = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A)
    other_rows = tools.search_commitments(ctx, workspace_id=other_ws.id, user_id=USER_A)
    assert len(default_rows) == 1 and len(other_rows) == 1
    assert default_rows[0].id != other_rows[0].id


# ---- Repository.list_user_owned is only available where explicitly configured ------------------------

def test_list_user_owned_is_not_available_on_repositories_without_a_configured_index(ctx):
    with pytest.raises(ValueError):
        ctx.repos.actions.list_user_owned(ctx.default_workspace_id, USER_A)


# ---- performance regression: user-scoped list must be an indexed query, never a workspace scan --------

def test_search_commitments_uses_the_index_not_a_workspace_wide_read(ctx, monkeypatch):
    """Locks in the fix: this must never regress back to `Repository.list(workspace_id,
    user_id=...)` (a full read of every commitment in the workspace, filtered in Python)."""
    tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A, text="I'll call Sam today.")

    store = ctx.repos.commitments._store
    calls = {"query": 0, "query_index": 0}
    orig_query, orig_query_index = store.query, store.query_index

    def spy_query(entity, workspace_id):
        if entity == "commitment":
            calls["query"] += 1
        return orig_query(entity, workspace_id)

    def spy_query_index(index_name, partition_key, **kwargs):
        calls["query_index"] += 1
        return orig_query_index(index_name, partition_key, **kwargs)

    monkeypatch.setattr(store, "query", spy_query)
    monkeypatch.setattr(store, "query_index", spy_query_index)

    tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A)

    assert calls["query"] == 0, "search_commitments must not read the whole workspace's commitments"
    assert calls["query_index"] == 1


def test_list_integration_accounts_uses_the_index_not_a_workspace_wide_read(ctx, monkeypatch):
    tools.connect_integration_account(
        ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A, provider="gmail", account_identifier="a@example.com"
    )

    store = ctx.repos.integration_accounts._store
    calls = {"query": 0, "query_index": 0}
    orig_query, orig_query_index = store.query, store.query_index

    def spy_query(entity, workspace_id):
        if entity == "integration_account":
            calls["query"] += 1
        return orig_query(entity, workspace_id)

    def spy_query_index(index_name, partition_key, **kwargs):
        calls["query_index"] += 1
        return orig_query_index(index_name, partition_key, **kwargs)

    monkeypatch.setattr(store, "query", spy_query)
    monkeypatch.setattr(store, "query_index", spy_query_index)

    tools.list_integration_accounts(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_A)

    assert calls["query"] == 0, "list_integration_accounts must not read the whole workspace's accounts"
    assert calls["query_index"] == 1


# ---- disconnect ownership check still works through the same store --------------------------------

def test_disconnect_cross_user_still_rejected_with_indexed_listing_in_place(ctx):
    account = _integration_account(ctx, USER_A)
    with pytest.raises(WorkspaceAccessError):
        tools.disconnect_integration_account(ctx, workspace_id=ctx.default_workspace_id, user_id=USER_B, account_id=account.id)
