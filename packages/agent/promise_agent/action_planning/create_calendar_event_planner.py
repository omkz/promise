from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from promise_domain.enums import ActionStatus, ActionType
from promise_domain.models import Action, Commitment, Contact
from promise_integrations.calendar.config import DEFAULT_EVENT_DURATION_MINUTES
from promise_shared.ids import new_id

from ..context import AgentRepos
from ..context_retrieval import ContextItem, ContextItemType
from . import _signals
from .planner import PlanningError
from .schema import ActionPlan

"""A commitment that reads as "meet/schedule [someone]" -- proposes a
`CREATE_CALENDAR_EVENT` `Action`, never creates the real Google Calendar
event itself (that's `execute_action`'s job, gated by approval like every
other action type; see `promise_agent.steps.execution`).

Nothing Andi/Friday-specific is hard-coded: `supports()` recognizes the
general shape (a meeting-type verb -- see `action_planning._signals`), and
`plan()` reads whatever `commitment.due_at` the extraction engine already
resolved (see `promise_agent.commitment_extraction.temporal` -- this planner
never invents or re-parses a date/time itself, and never lets an LLM guess
one). Calendar context items (existing events found by `CalendarSearchProvider`
during retrieval) are informational only in v1 -- nothing here currently
detects or avoids a conflicting event; see this class's own docstring for
that limitation.
"""


class CreateCalendarEventPlanner:
    """Prepares a proposed calendar event for a "meet/schedule X" commitment,
    and proposes a `CREATE_CALENDAR_EVENT` `Action` — never calls Google
    Calendar, never requests approval, never completes the commitment.

    Known v1 limitation: does not check `context_items` for a conflicting
    existing event before proposing a time — the proposed action's own
    summary/rationale always shows the exact time so a human approver can
    catch a conflict themselves; this planner does not attempt to.
    """

    name = "create_calendar_event"

    def supports(self, commitment: Commitment) -> bool:
        return _signals.has_meeting_verb(commitment.action)

    def plan(
        self, commitment: Commitment, contact: Contact | None, context_items: list[ContextItem],
        *, repos: AgentRepos, agent_run_id: str,
    ) -> dict[str, Any]:
        if not commitment.due_at:
            # Never invent a start time -- a "meet Andi" with no resolvable date/time
            # at all can't be turned into a calendar event.
            raise PlanningError("No date/time could be resolved for this meeting — nothing to schedule yet.")

        start_at = commitment.due_at
        end_at = _add_minutes(start_at, DEFAULT_EVENT_DURATION_MINUTES)
        timezone = os.getenv("PROMISE_TIMEZONE", "Asia/Jakarta")

        # Contact safety (see the README's "Attendee safety" note): a contact with no
        # verified email is never invited by guessing one -- the event is still
        # proposed (a personal calendar entry is useful on its own), just without an
        # attendee, and the rationale says so explicitly rather than hiding it.
        attendees: list[str] = []
        attendee_note = ""
        if contact is not None:
            if contact.email:
                attendees = [contact.email]
            else:
                attendee_note = f" {contact.name} will not be invited automatically — no verified email is on file."

        summary = f"Meeting with {contact.name}" if contact else (commitment.title or commitment.action or "Meeting")
        description = commitment.description or ""
        location = None  # never invented — see the "do not invent location" rule below

        supporting_ids = [i.id for i in context_items if i.type == ContextItemType.CALENDAR_EVENT][:3]

        action_plan = ActionPlan.for_action_type(
            ActionType.CREATE_CALENDAR_EVENT,
            summary=f"{summary}\n{_format_range(start_at, end_at)}\nCalendar: Primary",
            rationale=(
                f"Commitment action \"{commitment.action}\" matches a meeting/scheduling workflow; "
                f"using the commitment's own resolved date/time ({start_at}) and a default "
                f"{DEFAULT_EVENT_DURATION_MINUTES}-minute duration (no duration was stated explicitly)."
                + attendee_note
            ),
            target_contact_id=contact.id if contact else None,
            supporting_context_ids=supporting_ids,
            payload={
                "title": summary,
                "description": description,
                "start_at": start_at,
                "end_at": end_at,
                "timezone": timezone,
                "location": location,
                "attendees": attendees,
                "calendar_id": "primary",
            },
        )

        action = Action(
            id=new_id("act"),
            workspace_id=commitment.workspace_id,
            commitment_id=commitment.id,
            agent_run_id=agent_run_id,
            type=action_plan.action_type,
            status=ActionStatus.PROPOSED,
            payload=action_plan.payload,
            idempotency_key=f"create_calendar_event:{commitment.id}",
        )
        repos.actions.save(action)

        return {"action_plan": action_plan, "action": action}


def _add_minutes(iso_timestamp: str, minutes: int) -> str:
    start = datetime.fromisoformat(iso_timestamp)
    return (start + timedelta(minutes=minutes)).isoformat()


def _format_range(start_at: str, end_at: str) -> str:
    start = datetime.fromisoformat(start_at)
    end = datetime.fromisoformat(end_at)
    same_day = start.date() == end.date()
    if same_day:
        return f"{start.strftime('%A %-I:%M %p')}–{end.strftime('%-I:%M %p')}"
    return f"{start.strftime('%A %-I:%M %p')} – {end.strftime('%A %-I:%M %p')}"
