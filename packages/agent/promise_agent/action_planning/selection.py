from __future__ import annotations

from promise_domain.models import Commitment

from .planner import ActionPlanner, PlanningError
from .send_message_planner import SendMessagePlanner

"""Deterministic planner selection — no LLM. MVP ships exactly one planner;
adding another is registering it here, in order, and giving it its own
`supports()` check. The first planner whose `supports()` returns true wins.
"""

_PLANNERS: list[ActionPlanner] = [SendMessagePlanner()]


def select_planner(commitment: Commitment) -> ActionPlanner:
    """Raises `PlanningError` — never fabricates an action — for any commitment
    none of the registered planners recognize."""
    for planner in _PLANNERS:
        if planner.supports(commitment):
            return planner
    raise PlanningError("PROMISE does not yet know how to execute this commitment.")
