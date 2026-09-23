from __future__ import annotations

from typing import Any

from promise_domain.models import Commitment, Contact

from ..action_planning import PlanningError, SendMessagePlanner
from ..context import AgentRepos
from ..context_retrieval import ContextItem

"""Backward-compatible entry point for the one planner v1 ships.

The actual generic planning engine lives in `promise_agent.action_planning`
(`ActionPlanner`, `select_planner`, `SendMessagePlanner`, ...) —
`AgentOrchestrator` calls `select_planner()` directly now. This function
remains only because it's a stable, specifically-`SendMessagePlanner` entry
point some callers (and tests) still use.
"""

__all__ = ["PlanningError", "plan_send_revised_document"]


def plan_send_revised_document(
    commitment: Commitment, contact: Contact | None, context_items: list[ContextItem],
    *, repos: AgentRepos, agent_run_id: str,
) -> dict[str, Any]:
    return SendMessagePlanner().plan(commitment, contact, context_items, repos=repos, agent_run_id=agent_run_id)
