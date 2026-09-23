from __future__ import annotations

import inspect

import pytest
from promise_app import tools
from promise_domain.models import Workspace
from promise_shared.errors import NotFoundError, WorkspaceAccessError
from promise_shared.ids import new_id

"""Application/agent/REST/MCP-level tests for the Context Retrieval Engine. See
`tests/test_context_retrieval.py` for engine-only (provider/ranking/filtering) tests."""


# ---- 13. commitment -> context retrieval integration -------------------------------------------

def test_retrieve_commitment_context_integration_andi_demo(seeded_ctx):
    """The spec's own integration test: the Andi/Sarah demo scenario must surface the
    proposal doc and the Andi/Sarah messages as top results."""
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]

    result = tools.retrieve_commitment_context(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id
    )

    assert result["commitment"].id == commitment.id
    titles = [item.title for item in result["results"]]
    assert "Andi_Proposal_v3.docx" in titles
    assert titles.index("Andi_Proposal_v3.docx") < 3  # a clear top result, not buried

    senders = [item.metadata.get("sender") for item in result["results"] if item.type.value == "message"]
    assert "Andi" in senders or "Sarah" in senders  # at least one of the two relevant messages surfaced


def test_retrieve_commitment_context_does_not_mutate_the_commitment(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today.",
    )["commitment"]
    before = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, commitment_id=commitment.id)

    tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    after = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, commitment_id=commitment.id)
    assert before == after


# ---- 5/6. workspace + user isolation ------------------------------------------------------------

def test_retrieve_commitment_context_enforces_user_ownership(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today.",
    )["commitment"]

    with pytest.raises(WorkspaceAccessError):
        tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id="usr_someone_else", commitment_id=commitment.id)


def test_retrieve_commitment_context_enforces_workspace_isolation(seeded_ctx):
    ctx = seeded_ctx
    other_ws = new_id("ws")
    ctx.repos.workspaces.save(Workspace(id=other_ws, name="Other Co", slug="other-co"))

    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today.",
    )["commitment"]

    with pytest.raises(NotFoundError):
        tools.retrieve_commitment_context(ctx, workspace_id=other_ws, user_id=ctx.default_user_id, commitment_id=commitment.id)


# ---- 14. agent receives structured context, not a giant string ---------------------------------

def test_agent_planning_takes_structured_context_items_not_a_string():
    from promise_agent.steps import planning as planning_step

    sig = inspect.signature(planning_step.plan_send_revised_document)
    assert "context_items" in sig.parameters
    assert "documents" not in sig.parameters and "messages" not in sig.parameters


def test_full_handle_commitment_flow_works_end_to_end_with_the_new_retrieval_engine(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]

    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert handled["action"].status.value == "waiting_for_approval"
    assert handled["document"].content_text  # a real revision, produced from real retrieved content


# ---- REST ----------------------------------------------------------------------------------------

def test_rest_context_endpoint(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.post("/api/commitments", json={"text": "I'll send Andi the revised proposal tomorrow morning."})
        commitment_id = r.json()["commitment"]["id"]

        r = client.get(f"/api/commitments/{commitment_id}/context")
        assert r.status_code == 200
        body = r.json()
        assert body["commitment"]["id"] == commitment_id
        titles = [item["title"] for item in body["results"]]
        assert "Andi_Proposal_v3.docx" in titles
        assert body["status"] in ("complete", "partial")
    finally:
        app.dependency_overrides.clear()


def test_rest_context_endpoint_respects_workspace_header_isolation(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.post("/api/commitments", json={"text": "I'll call Sam today."})
        commitment_id = r.json()["commitment"]["id"]

        r = client.get(f"/api/commitments/{commitment_id}/context", headers={"X-Workspace-Id": "ws_someone_else"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ---- MCP -----------------------------------------------------------------------------------------

def test_mcp_retrieve_commitment_context_tool(seeded_ctx, monkeypatch):
    import promise_mcp.server as mcp_server

    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    created = mcp_server.create_commitment(text="I'll send Andi the revised proposal tomorrow morning.")
    commitment_id = created.structured_content["commitment"]["id"]

    result = mcp_server.retrieve_commitment_context(commitment_id)

    assert result["commitment"]["id"] == commitment_id
    titles = [item["title"] for item in result["results"]]
    assert "Andi_Proposal_v3.docx" in titles
