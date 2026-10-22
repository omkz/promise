from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

import pytest
from promise_agent.strands_agent import (
    StrandsAgentService,
    StrandsConfig,
    StrandsModelFactory,
    build_strands_tools,
    load_strands_config,
    validate_strands_config,
)
from promise_app import tools
from promise_domain.enums import ActionStatus, ApprovalStatus
from promise_shared.errors import (
    ApprovalRequiredError,
    StrandsConfigurationError,
    StrandsDisabledError,
    StrandsExecutionError,
)
from strands.models.anthropic import AnthropicModel
from strands.models.bedrock import BedrockModel
from strands.models.gemini import GeminiModel
from strands.models.model import Model
from strands.models.openai import OpenAIModel
from strands.types.streaming import StreamEvent

# ---- Mock Model for offline testing without network/API keys -----------------------------


class MockStrandsModel(Model):
    """Deterministic in-memory mock Model for testing Strands Agent orchestration."""

    def __init__(self, responses: list[list[StreamEvent]] | None = None) -> None:
        self.config = {"model_id": "mock-model"}
        self._responses = responses or [
            [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockStart": {"start": {}}},
                {"contentBlockDelta": {"delta": {"text": "I have checked your commitments."}}},
                {"contentBlockStop": {}},
                {"messageStop": {"stopReason": "end_turn"}},
            ]
        ]
        self.call_count = 0

    def get_config(self) -> dict[str, Any]:
        return self.config

    def update_config(self, **kwargs: Any) -> None:
        self.config.update(kwargs)

    async def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(
        self,
        messages: list[Any],
        tool_specs: list[Any] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        if self.call_count < len(self._responses):
            events = self._responses[self.call_count]
        else:
            events = [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockStart": {"start": {}}},
                {"contentBlockDelta": {"delta": {"text": "Finished."}}},
                {"contentBlockStop": {}},
                {"messageStop": {"stopReason": "end_turn"}},
            ]
        self.call_count += 1
        for event in events:
            yield event


class FailingMockModel(Model):
    """Mock model that raises an error during stream."""

    def __init__(self, error_message: str = "Simulated provider timeout or API error") -> None:
        self.config = {"model_id": "failing-mock-model"}
        self.error_message = error_message

    def get_config(self) -> dict[str, Any]:
        return self.config

    def update_config(self, **kwargs: Any) -> None:
        self.config.update(kwargs)

    async def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(
        self,
        messages: list[Any],
        tool_specs: list[Any] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        raise RuntimeError(self.error_message)
        yield  # Make it an async generator


# ---- Configuration and Validation Tests ---------------------------------------------------


def test_strands_disabled_by_default(seeded_ctx, monkeypatch):
    """Strands agent is disabled by default; attempting to invoke it raises StrandsDisabledError."""
    monkeypatch.delenv("STRANDS_ENABLED", raising=False)
    with pytest.raises(StrandsDisabledError) as exc_info:
        tools.invoke_strands_agent(
            seeded_ctx,
            workspace_id=seeded_ctx.default_workspace_id,
            user_id=seeded_ctx.default_user_id,
            prompt="Hello agent",
        )
    assert "Strands agent is disabled" in str(exc_info.value)


def test_strands_configuration_loading(monkeypatch):
    """Verifies environment variables load into StrandsConfig with proper defaults."""
    monkeypatch.setenv("STRANDS_ENABLED", "true")
    monkeypatch.setenv("STRANDS_MODEL_PROVIDER", "google")
    monkeypatch.setenv("STRANDS_MODEL_ID", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-gemini")

    cfg = load_strands_config()
    assert cfg.enabled is True
    assert cfg.provider == "google"
    assert cfg.model_id == "gemini-2.5-flash"
    assert cfg.gemini_api_key == "test-key-gemini"


def test_strands_configuration_loading_groq(monkeypatch):
    """Verifies Groq configuration defaults to openai/gpt-oss-120b and reads GROQ_API_KEY."""
    monkeypatch.setenv("STRANDS_ENABLED", "true")
    monkeypatch.setenv("STRANDS_MODEL_PROVIDER", "groq")
    monkeypatch.delenv("STRANDS_MODEL_ID", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key-groq")

    cfg = load_strands_config()
    assert cfg.enabled is True
    assert cfg.provider == "groq"
    assert cfg.model_id == "openai/gpt-oss-120b"
    assert cfg.groq_api_key == "test-key-groq"


def test_strands_configuration_validation_errors():
    """Misconfigured providers or missing credentials must raise StrandsConfigurationError."""
    # 1. Unsupported provider
    with pytest.raises(StrandsConfigurationError) as exc:
        validate_strands_config(StrandsConfig(enabled=True, provider="unknown_ai"))
    assert "Unsupported model provider 'unknown_ai'" in str(exc.value)

    # 2. Google provider missing GEMINI_API_KEY
    with pytest.raises(StrandsConfigurationError) as exc:
        validate_strands_config(StrandsConfig(enabled=True, provider="google", gemini_api_key=None))
    assert "GEMINI_API_KEY is required" in str(exc.value)

    # 3. OpenAI provider missing OPENAI_API_KEY
    with pytest.raises(StrandsConfigurationError) as exc:
        validate_strands_config(StrandsConfig(enabled=True, provider="openai", openai_api_key=None))
    assert "OPENAI_API_KEY is required" in str(exc.value)

    # 4. Anthropic provider missing ANTHROPIC_API_KEY
    with pytest.raises(StrandsConfigurationError) as exc:
        validate_strands_config(StrandsConfig(enabled=True, provider="anthropic", anthropic_api_key=None))
    assert "ANTHROPIC_API_KEY is required" in str(exc.value)

    # 5. Groq provider missing GROQ_API_KEY
    with pytest.raises(StrandsConfigurationError) as exc:
        validate_strands_config(StrandsConfig(enabled=True, provider="groq", groq_api_key=None))
    assert "GROQ_API_KEY is required" in str(exc.value)


def test_strands_model_factory():
    """Model factory creates the right Model instance for each supported provider."""
    # Google Gemini
    g_cfg = StrandsConfig(enabled=True, provider="google", model_id="gemini-2.5-flash", gemini_api_key="fake-gemini")
    g_model = StrandsModelFactory.create_model(g_cfg)
    assert isinstance(g_model, GeminiModel)
    assert g_model.get_config()["model_id"] == "gemini-2.5-flash"

    # OpenAI
    o_cfg = StrandsConfig(enabled=True, provider="openai", model_id="gpt-4o", openai_api_key="fake-openai")
    o_model = StrandsModelFactory.create_model(o_cfg)
    assert isinstance(o_model, OpenAIModel)
    assert o_model.get_config()["model_id"] == "gpt-4o"

    # Anthropic
    a_cfg = StrandsConfig(enabled=True, provider="anthropic", model_id="claude-3-5-sonnet-latest", anthropic_api_key="fake-anthropic")
    a_model = StrandsModelFactory.create_model(a_cfg)
    assert isinstance(a_model, AnthropicModel)
    assert a_model.get_config()["model_id"] == "claude-3-5-sonnet-latest"

    # Bedrock
    b_cfg = StrandsConfig(enabled=True, provider="bedrock", model_id="anthropic.claude-sonnet-4-20250514-v1:0", aws_region="us-east-1")
    b_model = StrandsModelFactory.create_model(b_cfg)
    assert isinstance(b_model, BedrockModel)
    assert b_model.get_config()["model_id"] == "anthropic.claude-sonnet-4-20250514-v1:0"

    # Groq (reuses OpenAIModel with Groq base_url)
    gr_cfg = StrandsConfig(enabled=True, provider="groq", model_id="openai/gpt-oss-120b", groq_api_key="fake-groq")
    gr_model = StrandsModelFactory.create_model(gr_cfg)
    assert isinstance(gr_model, OpenAIModel)
    assert gr_model.get_config()["model_id"] == "openai/gpt-oss-120b"
    assert gr_model.client_args == {
        "api_key": "fake-groq",
        "base_url": "https://api.groq.com/openai/v1",
    }


# ---- Tool Registry & Authorization Safety --------------------------------------------------


def test_strands_tool_registry_and_scoping(seeded_ctx):
    """Verifies exposed tools and asserts approval/execution tools are strictly omitted."""
    tools_list = build_strands_tools(
        seeded_ctx,
        workspace_id=seeded_ctx.default_workspace_id,
        user_id=seeded_ctx.default_user_id,
    )
    tool_names = [t.tool_name for t in tools_list]

    # Explicitly allowed tools
    assert "create_commitment" in tool_names
    assert "list_commitments" in tool_names
    assert "get_commitment" in tool_names
    assert "handle_commitment" in tool_names
    assert "list_pending_approvals" in tool_names
    assert "get_action" in tool_names
    assert "list_contacts" in tool_names
    assert "list_documents" in tool_names

    # MUST NOT be exposed to the model under any circumstances
    assert "decide_approval" not in tool_names
    assert "execute_approved_action" not in tool_names
    assert "execute_action" not in tool_names


def test_strands_tools_execution_and_approval_gating(seeded_ctx):
    """Exercises tool functions and verifies action enters waiting_for_approval without executing."""
    tools_by_name = {
        t.tool_name: t
        for t in build_strands_tools(
            seeded_ctx,
            workspace_id=seeded_ctx.default_workspace_id,
            user_id=seeded_ctx.default_user_id,
        )
    }

    # 1. create_commitment tool
    create_fn = tools_by_name["create_commitment"]
    create_res = create_fn("I'll send Andi the revised proposal tomorrow morning.")
    assert create_res["detected"] is True
    assert create_res["persisted"] is True
    commitment_id = create_res["commitment_id"]
    assert commitment_id is not None

    # 2. list_commitments tool
    list_fn = tools_by_name["list_commitments"]
    commitments = list_fn()
    assert any(c["id"] == commitment_id for c in commitments)

    # 3. handle_commitment tool
    handle_fn = tools_by_name["handle_commitment"]
    handle_res = handle_fn(commitment_id)
    assert handle_res["action_status"] == ActionStatus.WAITING_FOR_APPROVAL.value
    assert handle_res["approval_status"] == ApprovalStatus.PENDING.value
    action_id = handle_res["action_id"]

    # 4. Gating check: attempt to execute before human approval MUST fail
    with pytest.raises(ApprovalRequiredError):
        tools.execute_approved_action(
            seeded_ctx,
            workspace_id=seeded_ctx.default_workspace_id,
            action_id=action_id,
            actor=seeded_ctx.default_user_id,
        )


# ---- Agent Orchestration & Failure Handling ------------------------------------------------


def test_strands_orchestration_with_mock_model(seeded_ctx):
    """Tests successful StrandsAgentService invocation with MockStrandsModel."""
    config = StrandsConfig(enabled=True, provider="google", model_id="gemini-2.5-flash", gemini_api_key="fake-key")
    mock_model = MockStrandsModel()

    service = StrandsAgentService(default_config=config)
    res = service.invoke(
        seeded_ctx,
        workspace_id=seeded_ctx.default_workspace_id,
        user_id=seeded_ctx.default_user_id,
        prompt="List my open commitments",
        config=config,
        model=mock_model,
    )

    assert res.output_text == "I have checked your commitments."
    assert res.model_provider == "google"
    assert res.model_id == "gemini-2.5-flash"
    assert res.stop_reason == "end_turn"

    # Verify audit events were recorded
    events = tools.list_audit_events(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id)
    event_types = [e.event_type for e in events]
    assert "strands_agent.invoked" in event_types
    assert "strands_agent.completed" in event_types


def test_strands_agent_tool_invocation_loop(seeded_ctx):
    """Tests that Strands agent calls tools, receives tool output, and returns final answer."""
    import json

    tool_calling_responses = [
        # Turn 1: Model requests create_commitment tool
        [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockStart": {"start": {"toolUse": {"name": "create_commitment", "toolUseId": "call_commit_1"}}}},
            {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"text": "I'll send Andi the revised proposal tomorrow morning."})}}}},
            {"contentBlockStop": {}},
            {"messageStop": {"stopReason": "tool_use"}},
        ],
        # Turn 2: After tool execution, model produces final user message
        [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockStart": {"start": {}}},
            {"contentBlockDelta": {"delta": {"text": "I have created the commitment for Andi."}}},
            {"contentBlockStop": {}},
            {"messageStop": {"stopReason": "end_turn"}},
        ],
    ]

    config = StrandsConfig(enabled=True, provider="google", model_id="gemini-2.5-flash", gemini_api_key="fake-key")
    tool_model = MockStrandsModel(responses=tool_calling_responses)

    service = StrandsAgentService(default_config=config)
    res = service.invoke(
        seeded_ctx,
        workspace_id=seeded_ctx.default_workspace_id,
        user_id=seeded_ctx.default_user_id,
        prompt="Please remember to send Andi the proposal tomorrow morning",
        config=config,
        model=tool_model,
    )

    assert "I have created the commitment for Andi." in res.output_text
    assert tool_model.call_count == 2

    # Verify commitment was actually persisted in the database via the tool
    open_commitments = tools.search_commitments(
        seeded_ctx,
        workspace_id=seeded_ctx.default_workspace_id,
        user_id=seeded_ctx.default_user_id,
    )
    assert any("Andi" in c.description for c in open_commitments)


def test_strands_groq_orchestration_with_mock_model(seeded_ctx):
    """Tests StrandsAgentService invocation with provider='groq' using MockStrandsModel."""
    config = StrandsConfig(
        enabled=True,
        provider="groq",
        model_id="openai/gpt-oss-120b",
        groq_api_key="fake-groq-key",
    )
    mock_model = MockStrandsModel()

    service = StrandsAgentService(default_config=config)
    res = service.invoke(
        seeded_ctx,
        workspace_id=seeded_ctx.default_workspace_id,
        user_id=seeded_ctx.default_user_id,
        prompt="List my commitments using Groq",
        config=config,
        model=mock_model,
    )

    assert res.output_text == "I have checked your commitments."
    assert res.model_provider == "groq"
    assert res.model_id == "openai/gpt-oss-120b"
    assert res.stop_reason == "end_turn"

    # Verify audit event carries groq provider
    events = tools.list_audit_events(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id)
    invoked_events = [e for e in events if e.event_type == "strands_agent.invoked"]
    assert any("groq" in e.summary for e in invoked_events)




def test_strands_provider_failure_handling(seeded_ctx):
    """Provider failure or timeout raises StrandsExecutionError and records audit event."""
    config = StrandsConfig(enabled=True, provider="google", model_id="gemini-2.5-flash", gemini_api_key="fake-key")
    failing_model = FailingMockModel("Simulated Gemini quota exceeded")

    service = StrandsAgentService(default_config=config)
    with pytest.raises(StrandsExecutionError) as exc_info:
        service.invoke(
            seeded_ctx,
            workspace_id=seeded_ctx.default_workspace_id,
            user_id=seeded_ctx.default_user_id,
            prompt="Hello",
            config=config,
            model=failing_model,
        )

    assert "Simulated Gemini quota exceeded" in str(exc_info.value)

    events = tools.list_audit_events(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id)
    event_types = [e.event_type for e in events]
    assert "strands_agent.failed" in event_types


# ---- REST API and MCP Adapter Integration Tests -------------------------------------------


def test_strands_rest_api_endpoint(seeded_ctx, monkeypatch):
    """Tests POST /api/agent/strands/invoke."""
    from promise_api import deps
    from promise_api.main import app
    from starlette.testclient import TestClient

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx

    try:
        with TestClient(app) as client:
            # 1. Disabled: should return 400
            monkeypatch.delenv("STRANDS_ENABLED", raising=False)
            resp = client.post("/api/agent/strands/invoke", json={"prompt": "Hello"})
            assert resp.status_code == 400
            assert "Strands agent is disabled" in resp.json()["error"]
    finally:
        app.dependency_overrides.clear()


def test_strands_mcp_tool_invocation(seeded_ctx, monkeypatch):
    """Tests MCP tool invoke_strands_agent."""
    from promise_mcp import server as mcp_server

    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    monkeypatch.delenv("STRANDS_ENABLED", raising=False)

    # Calling tool when disabled raises StrandsDisabledError
    with pytest.raises(StrandsDisabledError):
        mcp_server.invoke_strands_agent(prompt="Hello MCP")
