from __future__ import annotations

from promise_domain.models import Commitment

from .planner import ActionPlanner, PlanningError
from .send_existing_document_planner import SendExistingDocumentPlanner
from .send_message_planner import SendRevisedDocumentPlanner

"""Deterministic planner selection — no LLM. MVP ships exactly two planners,
mutually exclusive by construction (one requires a revision signal, the other
requires its absence — see `action_planning._signals`), so registration order
doesn't affect correctness. Adding another planner is registering it here, in
order, and giving it its own `supports()` check. The first planner whose
`supports()` returns true wins.
"""

_PLANNERS: list[ActionPlanner] = [SendRevisedDocumentPlanner(), SendExistingDocumentPlanner()]


def select_planner(commitment: Commitment) -> ActionPlanner:
    """Raises `PlanningError` — never fabricates an action — for any commitment
    none of the registered planners recognize."""
    for planner in _PLANNERS:
        if planner.supports(commitment):
            return planner
    raise PlanningError("PROMISE does not yet know how to execute this commitment.")
