from __future__ import annotations

from typing import Any, Protocol

from promise_domain.models import Commitment, Contact
from promise_shared.errors import PromiseError

from ..context import AgentRepos
from ..context_retrieval import ContextItem

"""The `ActionPlanner` abstraction — generic action planning, separate from any
one specific workflow (the "send revised document" case lives entirely in
`SendMessagePlanner`, one implementation among possibly several later).
"""


class PlanningError(PromiseError):
    """Raised when a commitment can't be planned: no supported planner for its
    type (see `select_planner`), or a supported planner can't proceed (e.g. no
    relevant document found). Never a reason to fabricate an action."""


class ActionPlanner(Protocol):
    """Turns a commitment + its retrieved context into a proposed `Action`.

    An `ActionPlanner` must not: execute side effects (no `send_message`,
    only `create_draft`-style preparation), request approval, or mark a
    commitment complete — those stay in `steps/approval.py`,
    `steps/execution.py`, and `steps/completion.py` respectively, called by
    `AgentOrchestrator`, never by a planner. It must also never touch
    FastAPI/MCP or contain transport-specific logic — it only depends on
    `AgentRepos` (repositories + the current `IntegrationProvider`), exactly
    like every other agent step.
    """

    name: str

    def supports(self, commitment: Commitment) -> bool:
        """A cheap, deterministic check — no LLM — used by `select_planner`."""
        ...

    def plan(
        self, commitment: Commitment, contact: Contact | None, context_items: list[ContextItem],
        *, repos: AgentRepos, agent_run_id: str,
    ) -> dict[str, Any]:
        """Raises `PlanningError` (or `promise_shared.errors.VerifiedContactRequiredError`)
        when this commitment can't actually be planned, even though `supports()`
        said yes (e.g. no relevant document in `context_items`). Returns a dict
        that always includes `"action_plan"` (the `ActionPlan`) and `"action"`
        (the persisted, `PROPOSED` `Action`); planner-specific keys beyond that
        (e.g. `SendMessagePlanner`'s `"document"`/`"draft"`/`"changes"`) are
        preserved for the caller but not part of the generic contract."""
        ...
