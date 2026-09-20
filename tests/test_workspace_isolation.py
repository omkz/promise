from __future__ import annotations

from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_domain.models import Workspace
from promise_shared.errors import NotFoundError
from promise_shared.ids import new_id
import pytest


def _second_workspace(ctx: AppContext) -> str:
    ws = Workspace(id=new_id("ws"), name="Other Co", slug="other-co")
    ctx.repos.workspaces.save(ws)
    return ws.id


def test_commitments_do_not_leak_across_workspaces(ctx):
    other_ws = _second_workspace(ctx)

    tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today.")
    tools.create_commitment(ctx, workspace_id=other_ws, user_id="usr_other", text="I'll email Priya tomorrow.")

    default_rows = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id)
    other_rows = tools.search_commitments(ctx, workspace_id=other_ws)

    assert len(default_rows) == 1 and "Sam" in default_rows[0].description
    assert len(other_rows) == 1 and "Priya" in other_rows[0].description


def test_cannot_fetch_a_commitment_through_the_wrong_workspace(ctx):
    other_ws = _second_workspace(ctx)
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today."
    )["commitment"]

    with pytest.raises(NotFoundError):
        tools.get_commitment(ctx, workspace_id=other_ws, commitment_id=commitment.id)


def test_contacts_and_documents_are_workspace_scoped(seeded_ctx):
    ctx = seeded_ctx
    other_ws = _second_workspace(ctx)

    assert any(c.name == "Andi" for c in tools.list_contacts(ctx, workspace_id=ctx.default_workspace_id))
    assert tools.list_contacts(ctx, workspace_id=other_ws) == []
    assert tools.search_files(ctx, workspace_id=other_ws, query="proposal") == []
