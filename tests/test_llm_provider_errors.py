from __future__ import annotations

import pytest
from promise_agent import llm
from promise_shared.errors import LLMProviderError

"""Regression tests: BEDROCK_ENABLED=false must always use the deterministic
mock_revision, and BEDROCK_ENABLED=true must never silently fall back to the
mock when the real Bedrock call fails or returns something unusable — it must
raise a controlled, classified `LLMProviderError` instead (mirrors
tests/test_extraction_provider_errors.py's pattern for the commitment
extraction engine's Bedrock provider)."""


class _FakeBedrockError(Exception):
    """Stands in for `botocore.exceptions.ClientError` without needing botocore's
    real shape — just the `.response["Error"]["Code"]` attribute `llm._is_retryable`
    reads to classify retryability."""

    def __init__(self, code: str) -> None:
        super().__init__(f"simulated failure: {code}")
        self.response = {"Error": {"Code": code}}


def _patch_bedrock_client(monkeypatch, *, raises: Exception | None = None, converse_text: str | None = None):
    import boto3

    class _FakeClient:
        def converse(self, **kwargs):
            if raises is not None:
                raise raises
            return {"output": {"message": {"content": [{"text": converse_text}]}}}

    monkeypatch.setattr(boto3, "client", lambda *a, **kw: _FakeClient())


# ---- BEDROCK_ENABLED=false: mock is the configured behavior, not a fallback -------------------

def test_bedrock_disabled_uses_mock_directly(monkeypatch):
    monkeypatch.setenv("BEDROCK_ENABLED", "false")
    revised, changes = llm.revise_document("original text", "please update pricing and timeline")
    assert "REVISION NOTES" in revised
    assert changes


# ---- BEDROCK_ENABLED=true + failure: raise, never silently use the mock -----------------------

def test_bedrock_enabled_and_failing_raises_rather_than_silently_using_mock(monkeypatch):
    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError("InternalServerException"))

    with pytest.raises(LLMProviderError) as excinfo:
        llm.revise_document("original text", "feedback")
    assert excinfo.value.provider_name == "bedrock"


@pytest.mark.parametrize(
    "code,expected_retryable",
    [
        ("ThrottlingException", True),
        ("ServiceUnavailableException", True),
        ("InternalServerException", True),
        ("ValidationException", False),
        ("AccessDeniedException", False),
        ("ResourceNotFoundException", False),
    ],
)
def test_bedrock_error_retryability_is_classified_by_error_code(monkeypatch, code, expected_retryable):
    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError(code))

    with pytest.raises(LLMProviderError) as excinfo:
        llm.revise_document("original", "feedback")
    assert excinfo.value.retryable is expected_retryable


def test_unrecognized_transport_failure_defaults_to_retryable(monkeypatch):
    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    _patch_bedrock_client(monkeypatch, raises=TimeoutError("connection timed out"))

    with pytest.raises(LLMProviderError) as excinfo:
        llm.revise_document("original", "feedback")
    assert excinfo.value.retryable is True


def test_unparseable_bedrock_response_is_a_non_retryable_provider_error(monkeypatch):
    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    _patch_bedrock_client(monkeypatch, converse_text="not valid json at all")

    with pytest.raises(LLMProviderError) as excinfo:
        llm.revise_document("original", "feedback")
    assert excinfo.value.retryable is False


def test_bedrock_success_path_still_works(monkeypatch):
    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    _patch_bedrock_client(monkeypatch, converse_text='{"revised_text": "new text", "changes": ["did a thing"]}')

    revised, changes = llm.revise_document("original", "feedback")
    assert revised == "new text"
    assert changes == ["did a thing"]


# ---- application / REST level -------------------------------------------------------------------

def test_agent_run_fails_clearly_when_bedrock_fails_during_planning(seeded_ctx, monkeypatch):
    from promise_app import tools

    ctx = seeded_ctx
    # BEDROCK_ENABLED is a shared switch (both commitment extraction and document
    # revision read it) -- create the commitment first, with it still off, so only
    # the *planning* path below exercises the patched, failing Bedrock client.
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]

    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError("ThrottlingException"))

    with pytest.raises(LLMProviderError):
        tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    failed = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert failed.status.value == "failed"


def test_rest_api_returns_503_and_retryable_flag_on_llm_failure(seeded_ctx, monkeypatch):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        # BEDROCK_ENABLED is off for this call, so extraction uses the mock as usual.
        r = client.post("/api/commitments", json={"text": "I'll send Andi the revised proposal tomorrow morning."})
        commitment_id = r.json()["commitment"]["id"]

        monkeypatch.setenv("BEDROCK_ENABLED", "true")
        _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError("ThrottlingException"))

        r = client.post(f"/api/commitments/{commitment_id}/handle")
        assert r.status_code == 503
        body = r.json()
        assert body["provider"] == "bedrock"
        assert body["retryable"] is True
    finally:
        app.dependency_overrides.clear()
