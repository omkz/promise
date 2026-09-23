from __future__ import annotations

from typing import Any

from promise_domain.enums import ACTION_TYPES_REQUIRING_APPROVAL, ActionType
from pydantic import BaseModel, Field


class ActionPlan(BaseModel):
    """A planner's structured proposal — the seam between `ContextItem[]` and the
    persisted `Action` domain entity.

    Deliberately small and non-duplicative: `payload` is exactly what
    `Action.payload` will hold, and `action_type` maps directly to `Action.type`
    — nothing here re-states a field `Action` already owns (workspace_id,
    status, timestamps, idempotency_key, ...). Those are filled in once a
    planner turns this into a real `Action` and persists it; see
    `SendMessagePlanner.plan`.

    `approval_required` is informational/explainability only — it does not
    gate anything. The actual approval requirement is unconditional today
    (`AgentOrchestrator.run_handle_commitment` always requests approval,
    regardless of action type), and this field must never be used to bypass
    that; see `promise_domain.enums.ACTION_TYPES_REQUIRING_APPROVAL`, the
    reference set it's derived from.
    """

    action_type: ActionType
    summary: str
    rationale: str
    approval_required: bool = True
    target_contact_id: str | None = None
    supporting_context_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def for_action_type(cls, action_type: ActionType, **kwargs: Any) -> "ActionPlan":
        """Sets `approval_required` from the canonical reference set rather than
        letting a planner author decide it ad hoc."""
        return cls(action_type=action_type, approval_required=action_type in ACTION_TYPES_REQUIRING_APPROVAL, **kwargs)
