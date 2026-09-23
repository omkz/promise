from __future__ import annotations

import pytest
from promise_agent.commitment_extraction import BedrockCommitmentExtractionProvider, CommitmentExtractor
from promise_app import tools
from promise_shared.errors import ExtractionProviderError

"""Regression tests: BEDROCK_ENABLED=false must always use the deterministic mock, and
BEDROCK_ENABLED=true must never silently fall back to the mock when the real Bedrock
call fails — it must raise a controlled, classified `ExtractionProviderError` instead."""


class _FakeBedrockError(Exception):
    """Stands in for `botocore.exceptions.ClientError` without needing botocore's real
    shape — just the `.response["Error"]["Code"]` attribute the provider reads to
    classify retryability."""

    def __init__(self, code: str) -> None:
        super().__init__(f"simulated failure: {code}")
        self.response = {"Error": {"Code": code}}


def _patch_bedrock_client(monkeypatch, *, raises: Exception | None = None, converse_result: dict | None = None):
    import boto3

    class _FakeClient:
        def converse(self, **kwargs):
            if raises is not None:
                raise raises
            return converse_result

    monkeypatch.setattr(boto3, "client", lambda *a, **kw: _FakeClient())


# ---- provider-level: failures are raised, never silently swallowed into a fake result --------

def test_bedrock_failure_raises_instead_of_returning_none_or_a_mock_result(monkeypatch):
    _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError("InternalServerException"))
    provider = BedrockCommitmentExtractionProvider()

    with pytest.raises(ExtractionProviderError) as excinfo:
        provider.extract("I'll send Andi the proposal tomorrow.")
    assert excinfo.value.provider_name == "bedrock"


@pytest.mark.parametrize(
    "code,expected_retryable",
    [
        ("ThrottlingException", True),
        ("TooManyRequestsException", True),
        ("ServiceUnavailableException", True),
        ("InternalServerException", True),
        ("ValidationException", False),
        ("AccessDeniedException", False),
        ("ResourceNotFoundException", False),
    ],
)
def test_bedrock_error_retryability_is_classified_by_error_code(monkeypatch, code, expected_retryable):
    _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError(code))
    provider = BedrockCommitmentExtractionProvider()

    with pytest.raises(ExtractionProviderError) as excinfo:
        provider.extract("I'll send Andi the proposal tomorrow.")
    assert excinfo.value.retryable is expected_retryable


def test_unrecognized_transport_failure_defaults_to_retryable(monkeypatch):
    _patch_bedrock_client(monkeypatch, raises=TimeoutError("connection timed out"))
    provider = BedrockCommitmentExtractionProvider()

    with pytest.raises(ExtractionProviderError) as excinfo:
        provider.extract("I'll send Andi the proposal tomorrow.")
    assert excinfo.value.retryable is True


def test_missing_tool_use_output_is_a_non_retryable_provider_error(monkeypatch):
    _patch_bedrock_client(
        monkeypatch, converse_result={"output": {"message": {"content": [{"text": "not a tool call"}]}}}
    )
    provider = BedrockCommitmentExtractionProvider()

    with pytest.raises(ExtractionProviderError) as excinfo:
        provider.extract("I'll send Andi the proposal tomorrow.")
    assert excinfo.value.retryable is False


def test_invalid_structured_output_is_a_non_retryable_provider_error(monkeypatch):
    _patch_bedrock_client(
        monkeypatch,
        converse_result={
            "output": {"message": {"content": [{"toolUse": {"input": {"is_commitment": "not-a-bool"}}}]}}
        },
    )
    provider = BedrockCommitmentExtractionProvider()

    with pytest.raises(ExtractionProviderError) as excinfo:
        provider.extract("I'll send Andi the proposal tomorrow.")
    assert excinfo.value.retryable is False


# ---- extractor-level: explicitly-configured Bedrock never silently substitutes the mock -------

def test_extractor_propagates_the_provider_error_rather_than_falling_back_to_mock(monkeypatch):
    _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError("ThrottlingException"))
    extractor = CommitmentExtractor(BedrockCommitmentExtractionProvider())

    with pytest.raises(ExtractionProviderError):
        extractor.extract("I'll send Andi the proposal tomorrow.")


# ---- application level -------------------------------------------------------------------------

def test_bedrock_disabled_uses_mock_and_never_touches_bedrock(ctx, monkeypatch):
    monkeypatch.setenv("BEDROCK_ENABLED", "false")
    result = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today."
    )
    assert result["extraction"].provider_name == "mock"


def test_bedrock_enabled_and_failing_raises_rather_than_silently_using_mock(ctx, monkeypatch):
    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError("InternalServerException"))

    with pytest.raises(ExtractionProviderError):
        tools.create_commitment(
            ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll call Sam today."
        )


# ---- REST level: a controlled, retryable-tagged error, not a 200/201 with fake data -----------

def test_rest_api_returns_503_and_a_retryable_flag_on_bedrock_failure(seeded_ctx, monkeypatch):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    _patch_bedrock_client(monkeypatch, raises=_FakeBedrockError("ThrottlingException"))

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.post("/api/commitments", json={"text": "I'll call Sam today."})
        assert r.status_code == 503
        body = r.json()
        assert body["provider"] == "bedrock"
        assert body["retryable"] is True
    finally:
        app.dependency_overrides.clear()
