from __future__ import annotations

import pytest
from promise_app import tools

"""Regression tests: `CommitmentSource.occurred_at` (when the utterance/message itself
happened) must stay distinct from `CommitmentSource.created_at` (when PROMISE actually
processed/persisted it) — the caller can supply the former; the latter is always
processing time and preserves whatever timezone offset the caller supplied."""


def test_occurred_at_defaults_to_processing_time_when_caller_has_none(ctx):
    result = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today."
    )
    source = result["source"]
    assert source.occurred_at is not None
    assert source.created_at is not None


def test_caller_supplied_occurred_at_is_preserved_and_distinct_from_created_at(ctx):
    utterance_time = "2024-03-01T08:15:00-05:00"  # a plausible Alexa transcript timestamp, days in the past
    result = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today.",
        occurred_at=utterance_time,
    )
    source = result["source"]
    assert source.occurred_at == utterance_time
    assert source.created_at != utterance_time  # created_at is processing time, never the caller's value


def test_occurred_at_timezone_offset_is_preserved_not_normalized(ctx):
    utterance_time = "2026-01-15T22:00:00+09:00"  # JST, deliberately not UTC/Z
    result = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today.",
        occurred_at=utterance_time,
    )
    assert result["source"].occurred_at == "2026-01-15T22:00:00+09:00"


def test_invalid_occurred_at_is_rejected(ctx):
    with pytest.raises(ValueError):
        tools.create_commitment(
            ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today.",
            occurred_at="not-a-timestamp",
        )


def test_rest_api_accepts_and_preserves_occurred_at(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.post("/api/commitments", json={"text": "I'll call Sam today.", "occurred_at": "2026-02-01T07:30:00-08:00"})
        assert r.status_code == 201
        body = r.json()
        assert body["source"]["occurred_at"] == "2026-02-01T07:30:00-08:00"
        assert body["source"]["created_at"] != "2026-02-01T07:30:00-08:00"
    finally:
        app.dependency_overrides.clear()


def test_rest_api_rejects_invalid_occurred_at_with_400(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.post("/api/commitments", json={"text": "I'll call Sam today.", "occurred_at": "garbage"})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
