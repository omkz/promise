from __future__ import annotations

from promise_domain.enums import ActionStatus
from promise_domain.models import Action
from promise_shared.clock import iso_now
from promise_shared.errors import ApprovalRequiredError, ConflictError, DuplicateActionError, IntegrationNotConnected

from ..context import AgentRepos


def execute_action(action_id: str, workspace_id: str, repos: AgentRepos, *, user_id: str) -> Action:
    """Execute a proposed side effect. Requires ActionStatus.APPROVED.

    Guards against duplicate execution three ways, from outermost to
    innermost: the domain-level status check right below (an already-EXECUTED
    action never even attempts the claim below), the atomic APPROVED ->
    EXECUTING claim right after it, and the integration provider's own
    idempotency_key check (protects against a retry after a timed-out
    response, per the reliability requirement -- see e.g.
    `GmailIntegrationProvider.send_message`'s own SENDING claim, the same
    pattern one layer down).

    The claim is the one that actually matters under real concurrency: two
    callers racing to execute the *same* action can both pass the first
    check (both read APPROVED), but `Repository.update(...,
    expected_status=ActionStatus.APPROVED)` is an atomic compare-and-set (see
    that method's docstring) -- only one caller's write can succeed against
    the row as it *actually* is at write time, not as either caller happened
    to read it. The loser's `Repository.update` raises `ConflictError`,
    translated to `DuplicateActionError` here (the existing, already-tested
    error for "this action is already being/has been handled") rather than
    ever reaching `provider.send_message`/`calendar.create_event` at all --
    the side effect itself is never attempted twice for the same action.

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
    if action.status in (ActionStatus.EXECUTED, ActionStatus.EXECUTING):
        # EXECUTING means some other caller's claim (below) is already in flight for
        # this exact action -- that's "already being/has been handled", the same
        # DuplicateActionError an EXECUTED action gets, never ApprovalRequiredError
        # (which would misleadingly suggest this action was never approved at all).
        raise DuplicateActionError(action_id)
    if action.status != ActionStatus.APPROVED:
        raise ApprovalRequiredError(action_id)

    try:
        repos.actions.update(
            workspace_id, action_id, lambda a: setattr(a, "status", ActionStatus.EXECUTING),
            expected_status=ActionStatus.APPROVED,
        )
    except ConflictError as exc:
        # Someone else (a concurrent call, or a retry that's already in flight/done) won
        # the claim -- never attempt the side effect ourselves.
        raise DuplicateActionError(action_id) from exc

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
            workspace_id, action_id, lambda a: (setattr(a, "status", ActionStatus.FAILED), setattr(a, "error", error_message)),
            expected_status=ActionStatus.EXECUTING,
        )

    return repos.actions.update(
        workspace_id,
        action_id,
        lambda a: (
            setattr(a, "status", ActionStatus.EXECUTED),
            setattr(a, "result", result),
            setattr(a, "executed_at", iso_now()),
        ),
        expected_status=ActionStatus.EXECUTING,
    )
