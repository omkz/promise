from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promise_shared.errors import StrandsDisabledError, StrandsExecutionError
from strands import Agent

from promise_agent import audit

from .config import StrandsConfig, load_strands_config
from .model_factory import StrandsModelFactory
from .tools import build_strands_tools

if TYPE_CHECKING:
    from promise_app.bootstrap import AppContext
    from strands.models.model import Model


@dataclass(frozen=True)
class StrandsExecutionResult:
    prompt: str
    output_text: str
    model_provider: str
    model_id: str
    stop_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "output_text": self.output_text,
            "model_provider": self.model_provider,
            "model_id": self.model_id,
            "stop_reason": self.stop_reason,
        }


SYSTEM_PROMPT = (
    "You are PROMISE's follow-through AI assistant. "
    "You help users manage commitments, retrieve context, and prepare actions. "
    "You have access to tools for creating commitments, inspecting commitments, and handling commitments. "
    "SAFETY & GOVERNANCE RULES: "
    "- All operations are scoped strictly to the authenticated user and workspace. "
    "- You can propose actions by calling `handle_commitment`, which retrieves context and requests approval. "
    "- You CANNOT approve actions or execute external side effects directly. "
    "- External side effects (sending email, creating calendar events) strictly require human approval. "
    "- Never claim an external action was completed before explicit approval is recorded."
)


class StrandsAgentService:
    """Isolated agent service orchestrating Strands Agents SDK with model providers."""

    def __init__(self, default_config: StrandsConfig | None = None) -> None:
        self._default_config = default_config

    def invoke(
        self,
        ctx: AppContext,
        *,
        workspace_id: str,
        user_id: str,
        prompt: str,
        config: StrandsConfig | None = None,
        model: Model | None = None,
    ) -> StrandsExecutionResult:
        cfg = config or self._default_config or load_strands_config()

        if not cfg.enabled:
            raise StrandsDisabledError(
                "Strands agent is disabled. Set STRANDS_ENABLED=true in the environment to enable."
            )

        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise StrandsExecutionError("Agent prompt cannot be empty.")

        resolved_model = model or StrandsModelFactory.create_model(cfg)
        tools = build_strands_tools(ctx, workspace_id=workspace_id, user_id=user_id)

        audit.record(
            ctx.agent_repos,
            workspace_id=workspace_id,
            actor=user_id,
            event_type="strands_agent.invoked",
            entity_type="strands_agent",
            entity_id=user_id,
            summary=f"Strands agent invoked via provider '{cfg.provider}' and model '{cfg.model_id}'",
        )

        try:
            agent = Agent(
                model=resolved_model,
                tools=tools,
                system_prompt=SYSTEM_PROMPT,
            )
            result = agent(clean_prompt)
        except Exception as exc:
            audit.record(
                ctx.agent_repos,
                workspace_id=workspace_id,
                actor=user_id,
                event_type="strands_agent.failed",
                entity_type="strands_agent",
                entity_id=user_id,
                summary=f"Strands agent execution failed: {exc}",
            )
            raise StrandsExecutionError(
                f"Strands agent execution failed with provider '{cfg.provider}': {exc}"
            ) from exc

        output_text = ""
        if hasattr(result, "message") and isinstance(result.message, dict):
            for block in result.message.get("content", []):
                if isinstance(block, dict) and "text" in block:
                    output_text += block["text"]

        stop_reason = getattr(result, "stop_reason", "end_turn") or "end_turn"

        audit.record(
            ctx.agent_repos,
            workspace_id=workspace_id,
            actor=user_id,
            event_type="strands_agent.completed",
            entity_type="strands_agent",
            entity_id=user_id,
            summary="Strands agent completed invocation successfully",
        )

        return StrandsExecutionResult(
            prompt=clean_prompt,
            output_text=output_text,
            model_provider=cfg.provider,
            model_id=cfg.model_id,
            stop_reason=str(stop_reason),
        )
