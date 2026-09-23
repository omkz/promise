from __future__ import annotations

import promise_mcp.server as mcp_server
from fastapi.testclient import TestClient
from promise_api import deps
from promise_api.main import app


def test_mcp_tools_are_thin_wrappers_over_the_shared_application_layer(seeded_ctx, monkeypatch):
    """@mcp.tool() leaves the underlying function directly callable, so this exercises
    the exact adapter code path (arg mapping + JSON-safe dump) without a live socket."""
    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)

    created = mcp_server.create_commitment(text="I'll send Andi the revised proposal tomorrow morning.")
    # create_commitment returns a CallToolResult directly (Milestone 2: carries _meta.ui.resourceUri).
    assert created.meta == {"ui": {"resourceUri": mcp_server.UI_RESOURCE_URI}}
    assert created.structured_content["commitment"]["status"] == "open"
    commitment_id = created.structured_content["commitment"]["id"]

    handled = mcp_server.handle_commitment(commitment_id)
    assert handled["action"]["status"] == "waiting_for_approval"

    approved = mcp_server.decide_approval(handled["approval"]["id"], "approved")
    assert approved["status"] == "approved"

    executed = mcp_server.execute_approved_action(handled["action"]["id"])
    assert executed["action"]["status"] == "executed"
    assert executed["commitment"]["status"] == "completed"

    rows = mcp_server.search_commitments(query="Andi")
    assert any(c["id"] == commitment_id for c in rows)


def test_mcp_health_route_is_served(seeded_ctx, monkeypatch):
    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    with TestClient(mcp_server.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["service"] == "promise-mcp"


def test_web_and_mcp_channels_see_the_same_workspace_state(seeded_ctx, monkeypatch):
    """The REST API and the MCP adapter both call promise_app.tools against the same
    AppContext — a commitment created over one channel must be visible on the other."""
    from promise_auth import AuthenticatedPrincipal, permissions_for_role

    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    app.dependency_overrides[deps.get_principal] = lambda: AuthenticatedPrincipal(
        subject=f"local:{seeded_ctx.default_user_id}", user_id=seeded_ctx.default_user_id,
        workspace_id=seeded_ctx.default_workspace_id, role="owner", permissions=permissions_for_role("owner"),
        scopes=frozenset({"*"}), auth_method="local",
    )
    try:
        client = TestClient(app)
        r = client.post("/api/commitments", json={"text": "I'll call Sam today."})
        commitment_id = r.json()["commitment"]["id"]

        via_mcp = mcp_server.search_commitments(query="Sam")
        assert any(c["id"] == commitment_id for c in via_mcp)

        mcp_created = mcp_server.create_commitment(text="I'll email Priya tomorrow.")
        via_rest = client.get("/api/commitments").json()
        assert any(c["id"] == mcp_created.structured_content["commitment"]["id"] for c in via_rest)
    finally:
        app.dependency_overrides.clear()
