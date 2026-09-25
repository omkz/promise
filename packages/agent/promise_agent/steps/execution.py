from __future__ import annotations

from promise_domain.enums import ActionStatus
from promise_domain.models import Action
from promise_shared.clock import iso_now
from promise_shared.errors import ApprovalRequiredError, DuplicateActionError, IntegrationNotConnected

from ..context import AgentRepos


def execute_action(action_id: str, workspace_id: str, repos: AgentRepos, *, user_id: str) -> Action:
    """Execute a proposed side effect. Requires ActionStatus.APPROVED.

    Guards against duplicate execution two ways: the domain-level status
    check here (an EXECUTED action can never be executed again) and the
    integration provider's own idempotency_key check (protects against a
    retry after a timed-out response, per the reliability requirement).

    `user_id` (the caller executing the action -- already ownership-verified
    by `tools.execute_approved_action` before this is ever reached) selects
    which provider actually performs the side effect: this user's own
    connected Gmail/Calendar account if they have one. `send_message` falls
    back to the default (local) provider when no Gmail account is connected,
    exactly as before Gmail existed; `create_calendar_event` has no local
    fallback (a calendar event is inherently external -- there's no local/demo
    calendar concept the way there's a local/demo Draft), so it raises
    `IntegrationNotConnected` outright when Calendar isn't connected, caught
    below and recorded as a normal failed execution, not a crash. The `Draft`
    a `send_message` action sends is always a PROMISE-local row regardless of
    which provider ends up sending it (see `AgentRepos.integration`'s own
    docstring) — this only changes who actually delivers/creates it.
    """
    action = repos.actions.require(workspace_id, action_id)
    if action.status == ActionStatus.EXECUTED:
        raise DuplicateActionError(action_id)
    if action.status != ActionStatus.APPROVED:
        raise ApprovalRequiredError(action_id)

    repos.actions.update(workspace_id, action_id, lambda a: setattr(a, "status", ActionStatus.EXECUTING))

    try:
        if action.type.value == "send_message":
            provider = repos.integration_registry.resolve_for_user(workspace_id, user_id, provider_name="gmail") or repos.integration
            result = provider.send_message(
                workspace_id, draft_id=action.payload["draft_id"], idempotency_key=action.idempotency_key
            )
        elif action.type.value == "create_calendar_event":
            calendar = repos.integration_registry.resolve_for_user(workspace_id, user_id, provider_name="google_calendar")
            if calendar is None:
                raise IntegrationNotConnected("google_calendar")
            payload = action.payload
            result = calendar.create_event(
                workspace_id,
                calendar_id=payload.get("calendar_id", "primary"),
                summary=payload["title"],
                description=payload.get("description", ""),
                start_at=payload["start_at"],
                end_at=payload["end_at"],
                timezone=payload["timezone"],
                location=payload.get("location"),
                attendees=payload.get("attendees") or None,
                idempotency_key=action.idempotency_key,
            )
        else:
            raise NotImplementedError(f"no executor for action type '{action.type}'")
    except Exception as exc:  # noqa: BLE001 - convert to a recorded failure, not a crash
        # Python implicitly deletes `exc` at the end of this `except` block, so it can't be
        # referenced from inside a closure defined here (ruff/pyflakes flags exactly that,
        # correctly) — capture the message as a plain local first.
        error_message = str(exc)
        return repos.actions.update(
            workspace_id, action_id, lambda a: (setattr(a, "status", ActionStatus.FAILED), setattr(a, "error", error_message))
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
