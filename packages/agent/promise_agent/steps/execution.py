from __future__ import annotations

from promise_domain.enums import ActionStatus
from promise_domain.models import Action
from promise_shared.clock import iso_now
from promise_shared.errors import ApprovalRequiredError, DuplicateActionError

from ..context import AgentRepos


def execute_action(action_id: str, workspace_id: str, repos: AgentRepos) -> Action:
    """Execute a proposed side effect. Requires ActionStatus.APPROVED.

    Guards against duplicate execution two ways: the domain-level status
    check here (an EXECUTED action can never be executed again) and the
    integration provider's own idempotency_key check (protects against a
    retry after a timed-out response, per the reliability requirement).
    """
    action = repos.actions.require(workspace_id, action_id)
    if action.status == ActionStatus.EXECUTED:
        raise DuplicateActionError(action_id)
    if action.status != ActionStatus.APPROVED:
        raise ApprovalRequiredError(action_id)

    repos.actions.update(workspace_id, action_id, lambda a: setattr(a, "status", ActionStatus.EXECUTING))

    try:
        if action.type.value == "send_message":
            result = repos.integration.send_message(
                workspace_id, draft_id=action.payload["draft_id"], idempotency_key=action.idempotency_key
            )
        else:
            raise NotImplementedError(f"no executor for action type '{action.type}'")
    except Exception as exc:  # noqa: BLE001 - convert to a recorded failure, not a crash
        return repos.actions.update(
            workspace_id, action_id, lambda a: (setattr(a, "status", ActionStatus.FAILED), setattr(a, "error", str(exc)))
        )

    return repos.actions.update(
        workspace_id,
        action_id,
        lambda a: (
            setattr(a, "status", ActionStatus.EXECUTED),
            setattr(a, "result", result),
            setattr(a, "executed_at", iso_now()),
        ),
    )
