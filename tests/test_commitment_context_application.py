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
    before = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    after = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
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


def test_retrieval_to_context_items_to_planning_to_proposed_action(seeded_ctx):
    """Regression coverage proving:
    retrieval -> ContextItem[] -> planning -> proposed Action

    Verifies:
    1. retrieval_step.retrieve_context() returns structured ContextItem[] under 'items',
       without fallback keys like 'documents' or 'messages'.
    2. planning_step.plan_send_revised_document() accepts context_items: list[ContextItem] directly.
    3. Planning internally identifies relevant items by ContextItem.type and produces a proposed Action.
    4. Passing legacy documents/messages keywords to planning fails with TypeError.
    5. Approval flow is preserved: the proposed Action can be requested, approved, and executed.
    """
    from promise_agent.context_retrieval import ContextItem, ContextItemType
    from promise_agent.steps import planning as planning_step
    from promise_agent.steps import retrieval as retrieval_step
    from promise_domain.enums import ActionStatus, ActionType

    ctx = seeded_ctx
    ws = ctx.default_workspace_id
    uid = ctx.default_user_id

    commitment = tools.create_commitment(
        ctx, workspace_id=ws, user_id=uid,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]
    contact = ctx.repos.contacts.get(ws, commitment.contact_id)

    # 1. Retrieval returns structured ContextItem[]
    retrieval_result = retrieval_step.retrieve_context(commitment, contact, ctx.agent_repos)
    assert "items" in retrieval_result
    assert "documents" not in retrieval_result
    assert "messages" not in retrieval_result

    items = retrieval_result["items"]
    assert isinstance(items, list)
    assert len(items) > 0
    assert all(isinstance(item, ContextItem) for item in items)
    item_types = {item.type for item in items}
    assert ContextItemType.DOCUMENT in item_types
    assert ContextItemType.MESSAGE in item_types

    # 2. Planning consumes ContextItem[] directly
    run_id = new_id("run")
    plan = planning_step.plan_send_revised_document(
        commitment,
        contact,
        context_items=items,
        repos=ctx.agent_repos,
        agent_run_id=run_id,
    )

    # Calling with legacy keyword args raises TypeError
    with pytest.raises(TypeError):
        planning_step.plan_send_revised_document(
            commitment, contact, documents=[], messages=[], repos=ctx.agent_repos, agent_run_id=run_id  # type: ignore[call-arg]
        )

    # 3. Planning yields a proposed Action
    action = plan["action"]
    assert action.status == ActionStatus.PROPOSED
    assert action.type == ActionType.SEND_MESSAGE
    assert action.commitment_id == commitment.id
    assert action.agent_run_id == run_id
    assert action.payload["draft_id"] == plan["draft"]["id"]
    assert action.payload["document_id"] == plan["document"].id
    assert action.idempotency_key == f"send_message:{plan['draft']['id']}"

    # Verify entities were persisted in repos
    stored_action = ctx.repos.actions.require(ws, action.id)
    assert stored_action.status == ActionStatus.PROPOSED

    stored_doc = ctx.repos.documents.require(ws, plan["document"].id)
    assert stored_doc.metadata.get("agent_generated") is True

    # 4. Preserve approval flow
    approval = tools.request_approval(ctx, workspace_id=ws, user_id=uid, action_id=action.id)
    assert approval.status.value == "pending"

    action_after_request = ctx.repos.actions.require(ws, action.id)
    assert action_after_request.status.value == "waiting_for_approval"

    tools.decide_approval(ctx, workspace_id=ws, approval_id=approval.id, decision="approved", decided_by=uid)
    executed = ctx.agent_repos.integration.send_message(
        ws, draft_id=plan["draft"]["id"], idempotency_key=action.idempotency_key
    )
    assert executed["draft"]["status"] == "sent"


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
