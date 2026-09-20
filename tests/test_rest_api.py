from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from promise_api import deps
from promise_api.main import app


@pytest.fixture
def client(seeded_ctx):
    # Only override context resolution; workspace_id/user_id still resolve for real
    # (header if present, else the context's default), so header-based tests are valid.
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"


def test_full_approval_flow_over_rest(client):
    r = client.post("/api/commitments", json={"text": "I'll send Andi the revised proposal tomorrow morning."})
    assert r.status_code == 201
    commitment_id = r.json()["commitment"]["id"]

    r = client.post(f"/api/commitments/{commitment_id}/handle")
    assert r.status_code == 200
    action_id = r.json()["action"]["id"]
    approval_id = r.json()["approval"]["id"]
    assert r.json()["action"]["status"] == "waiting_for_approval"

    # executing before approval is rejected, not silently allowed
    r = client.post(f"/api/actions/{action_id}/execute")
    assert r.status_code == 409

    r = client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "approved"})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    r = client.post(f"/api/actions/{action_id}/execute")
    assert r.status_code == 200 and r.json()["action"]["status"] == "executed"

    r = client.post(f"/api/actions/{action_id}/execute")
    assert r.status_code == 409  # duplicate execution blocked

    r = client.get(f"/api/commitments/{commitment_id}")
    assert r.json()["status"] == "completed"


def test_not_found_returns_404(client):
    r = client.get("/api/commitments/com_does_not_exist")
    assert r.status_code == 404


def test_workspace_header_isolates_data(client, seeded_ctx):
    client.post("/api/commitments", json={"text": "I'll call Sam today."})
    r = client.get("/api/commitments", headers={"X-Workspace-Id": "ws_someone_else"})
    assert r.json() == []


def test_pending_approvals_and_audit_endpoints(client):
    r = client.post("/api/commitments", json={"text": "I'll send Andi the revised proposal tomorrow morning."})
    commitment_id = r.json()["commitment"]["id"]
    client.post(f"/api/commitments/{commitment_id}/handle")

    pending = client.get("/api/approvals/pending").json()
    assert len(pending) == 1

    audit = client.get("/api/audit-events").json()
    assert len(audit) >= 2
