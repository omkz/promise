from __future__ import annotations

import pytest
from promise_app import tools
from promise_shared.errors import WorkspaceAccessError

"""Regression tests: personal commitment resources are user-owned, not just
workspace-owned — workspace membership alone must never be enough to read or
act on someone else's commitment, even within the same workspace. See
`promise_app.tools._require_owned_commitment`/`_require_owned_action`.
"""

OWNER = "usr_owner"
OTHER = "usr_other"


def _create_as_owner(ctx, text="I'll send Andi the revised proposal tomorrow morning."):
    return tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, text=text)["commitment"]


# ---- single-commitment reads/writes -------------------------------------------------------------

def test_get_commitment_cross_user_is_rejected(ctx):
    commitment = _create_as_owner(ctx)
    with pytest.raises(WorkspaceAccessError):
        tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OTHER, commitment_id=commitment.id)


def test_get_commitment_by_the_owner_still_works(ctx):
    commitment = _create_as_owner(ctx)
    fetched = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)
    assert fetched.id == commitment.id


def test_update_commitment_cross_user_is_rejected(ctx):
    commitment = _create_as_owner(ctx)
    with pytest.raises(WorkspaceAccessError):
        tools.update_commitment(
            ctx, workspace_id=ctx.default_workspace_id, user_id=OTHER, commitment_id=commitment.id, title="Hijacked"
        )
    # never actually mutated
    unchanged = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)
    assert unchanged.title == commitment.title


def test_complete_commitment_cross_user_is_rejected(ctx):
    commitment = _create_as_owner(ctx)
    with pytest.raises(WorkspaceAccessError):
        tools.complete_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OTHER, commitment_id=commitment.id)
    unchanged = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)
    assert unchanged.status.value != "completed"


def test_cancel_commitment_cross_user_is_rejected(ctx):
    commitment = _create_as_owner(ctx)
    with pytest.raises(WorkspaceAccessError):
        tools.cancel_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OTHER, commitment_id=commitment.id)
    unchanged = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)
    assert unchanged.status.value != "cancelled"


def test_handle_commitment_cross_user_is_rejected(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, text="I'll send Andi the revised proposal tomorrow morning."
    )["commitment"]
    with pytest.raises(WorkspaceAccessError):
        tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OTHER, commitment_id=commitment.id)
    # no agent run was ever started for it
    assert tools.list_agent_runs(ctx, workspace_id=ctx.default_workspace_id) == []


def test_handle_commitment_by_the_owner_still_works(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, text="I'll send Andi the revised proposal tomorrow morning."
    )["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)
    assert handled["action"].status.value == "waiting_for_approval"


# ---- actions / approvals, ownership derived transitively via the commitment ----------------------

def test_decide_approval_cross_user_is_rejected(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, text="I'll send Andi the revised proposal tomorrow morning."
    )["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)

    with pytest.raises(WorkspaceAccessError):
        tools.decide_approval(
            ctx, workspace_id=ctx.default_workspace_id, approval_id=handled["approval"].id, decision="approved", decided_by=OTHER
        )
    # still pending -- the rejected decision never took effect
    approval = tools.list_pending_approvals(ctx, workspace_id=ctx.default_workspace_id)
    assert any(a.id == handled["approval"].id for a in approval)


def test_execute_approved_action_cross_user_is_rejected(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, text="I'll send Andi the revised proposal tomorrow morning."
    )["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)
    tools.decide_approval(
        ctx, workspace_id=ctx.default_workspace_id, approval_id=handled["approval"].id, decision="approved", decided_by=OWNER
    )

    with pytest.raises(WorkspaceAccessError):
        tools.execute_approved_action(ctx, workspace_id=ctx.default_workspace_id, action_id=handled["action"].id, actor=OTHER)

    action = tools.list_actions(ctx, workspace_id=ctx.default_workspace_id, commitment_id=commitment.id)[0]
    assert action.status.value != "executed"


def test_owner_can_still_decide_and_execute_their_own_action(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, text="I'll send Andi the revised proposal tomorrow morning."
    )["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)
    tools.decide_approval(
        ctx, workspace_id=ctx.default_workspace_id, approval_id=handled["approval"].id, decision="approved", decided_by=OWNER
    )
    result = tools.execute_approved_action(ctx, workspace_id=ctx.default_workspace_id, action_id=handled["action"].id, actor=OWNER)
    assert result["action"].status.value == "executed"


# ---- agent runs (own user_id field, no transitive lookup needed) ---------------------------------

def test_get_agent_run_cross_user_is_rejected(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, text="I'll send Andi the revised proposal tomorrow morning."
    )["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)

    with pytest.raises(WorkspaceAccessError):
        tools.get_agent_run(ctx, workspace_id=ctx.default_workspace_id, user_id=OTHER, agent_run_id=handled["agent_run"].id)


def test_get_agent_run_by_the_owner_still_works(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, text="I'll send Andi the revised proposal tomorrow morning."
    )["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, commitment_id=commitment.id)

    detail = tools.get_agent_run(ctx, workspace_id=ctx.default_workspace_id, user_id=OWNER, agent_run_id=handled["agent_run"].id)
    assert detail["run"].id == handled["agent_run"].id


# ---- REST level: cross-user is 403 ----------------------------------------------------------------

def test_rest_get_commitment_cross_user_is_403(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app
    from promise_auth import AuthenticatedPrincipal, permissions_for_role

    def _principal_for(user_id: str) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            subject=f"local:{user_id}", user_id=user_id, workspace_id=seeded_ctx.default_workspace_id, role="owner",
            permissions=permissions_for_role("owner"), scopes=frozenset({"*"}), auth_method="local",
        )

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        app.dependency_overrides[deps.get_principal] = lambda: _principal_for(OWNER)
        client = TestClient(app)
        r = client.post("/api/commitments", json={"text": "I'll send Andi the revised proposal tomorrow morning."})
        commitment_id = r.json()["commitment"]["id"]

        app.dependency_overrides[deps.get_principal] = lambda: _principal_for(OTHER)
        r = client.get(f"/api/commitments/{commitment_id}")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()
