from __future__ import annotations

import pytest
from promise_agent.action_planning.create_calendar_event_planner import CreateCalendarEventPlanner
from promise_agent.action_planning.planner import PlanningError
from promise_agent.action_planning.selection import select_planner
from promise_domain.models import Commitment, Contact
from promise_shared.ids import new_id

"""CreateCalendarEventPlanner -- pure planning: proposes a CREATE_CALENDAR_EVENT
Action, never calls Google Calendar, never requests approval, never invents
attendee emails/location/duration silently."""

WORKSPACE = "ws_1"
USER = "usr_1"


def _commitment(**overrides) -> Commitment:
    fields = {
        "id": new_id("cmt"), "workspace_id": WORKSPACE, "user_id": USER, "action": "Meet",
        "title": "Meet Andi", "description": "", "contact_id": None,
        "due_at": "2026-09-25T14:00:00+07:00",
    }
    fields.update(overrides)
    return Commitment(**fields)


def _contact(**overrides) -> Contact:
    fields = {"id": new_id("con"), "workspace_id": WORKSPACE, "name": "Andi", "email": None}
    fields.update(overrides)
    return Contact(**fields)


def test_select_planner_picks_calendar_planner_for_a_meeting_commitment():
    commitment = _commitment(action="Meet")
    assert isinstance(select_planner(commitment), CreateCalendarEventPlanner)


def test_select_planner_picks_calendar_planner_for_schedule_verb():
    commitment = _commitment(action="Schedule")
    assert isinstance(select_planner(commitment), CreateCalendarEventPlanner)


def test_select_planner_does_not_pick_calendar_planner_for_a_send_commitment():
    commitment = _commitment(action="Send")
    assert not isinstance(select_planner(commitment), CreateCalendarEventPlanner)


def test_plan_returns_an_action_plan_and_a_proposed_action_never_executed(ctx):
    commitment = _commitment()
    contact = _contact(email="andi@example.com")
    result = CreateCalendarEventPlanner().plan(commitment, contact, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))

    action_plan = result["action_plan"]
    action = result["action"]
    assert action_plan.action_type.value == "create_calendar_event"
    assert action.status.value == "proposed"  # never auto-created/executed
    assert action_plan.payload["start_at"] == "2026-09-25T14:00:00+07:00"
    assert action_plan.payload["calendar_id"] == "primary"


def test_plan_never_calls_a_calendar_provider(ctx):
    """The planner takes no CalendarProvider argument at all -- there is no
    object in its call signature it could call create_event on, which is the
    structural guarantee behind 'planning never creates the real event'."""
    import inspect

    sig = inspect.signature(CreateCalendarEventPlanner.plan)
    assert "calendar" not in sig.parameters
    assert "provider" not in sig.parameters


def test_plan_uses_default_duration_and_states_the_assumption_explicitly(ctx):
    commitment = _commitment()
    result = CreateCalendarEventPlanner().plan(commitment, None, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    action_plan = result["action_plan"]
    assert action_plan.payload["start_at"] == "2026-09-25T14:00:00+07:00"
    assert action_plan.payload["end_at"] == "2026-09-25T14:30:00+07:00"  # +30min default
    assert "30-minute duration" in action_plan.rationale
    assert "no duration was stated explicitly" in action_plan.rationale


def test_plan_uses_the_configured_default_duration_not_the_hard_coded_30(ctx, monkeypatch):
    """CALENDAR_DEFAULT_EVENT_DURATION_MINUTES=45 -> end_at is start_at + 45 minutes,
    proving the planner reads the actual configured value rather than a hard-coded
    30-minute module constant."""
    monkeypatch.setenv("CALENDAR_DEFAULT_EVENT_DURATION_MINUTES", "45")
    commitment = _commitment()
    result = CreateCalendarEventPlanner().plan(commitment, None, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    action_plan = result["action_plan"]
    assert action_plan.payload["start_at"] == "2026-09-25T14:00:00+07:00"
    assert action_plan.payload["end_at"] == "2026-09-25T14:45:00+07:00"
    assert "45-minute duration" in action_plan.rationale


def test_plan_with_no_due_at_raises_rather_than_inventing_a_time(ctx):
    commitment = _commitment(due_at=None)
    with pytest.raises(PlanningError):
        CreateCalendarEventPlanner().plan(commitment, None, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))


def test_plan_invites_contact_with_a_verified_email(ctx):
    commitment = _commitment()
    contact = _contact(email="andi@example.com")
    result = CreateCalendarEventPlanner().plan(commitment, contact, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    assert result["action_plan"].payload["attendees"] == ["andi@example.com"]


def test_plan_never_fabricates_an_attendee_email_when_contact_has_none(ctx):
    commitment = _commitment()
    contact = _contact(email=None)
    result = CreateCalendarEventPlanner().plan(commitment, contact, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    action_plan = result["action_plan"]
    assert action_plan.payload["attendees"] == []
    # the event is still proposed (never blocked outright), but the omission is explicit
    assert "will not be invited automatically" in action_plan.rationale
    assert "no verified email is on file" in action_plan.rationale


def test_plan_with_no_contact_at_all_proposes_a_plain_event(ctx):
    commitment = _commitment(contact_id=None)
    result = CreateCalendarEventPlanner().plan(commitment, None, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    action_plan = result["action_plan"]
    assert action_plan.payload["attendees"] == []
    assert action_plan.target_contact_id is None


def test_plan_never_invents_a_location(ctx):
    commitment = _commitment()
    result = CreateCalendarEventPlanner().plan(commitment, None, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    assert result["action_plan"].payload["location"] is None


def test_plan_summary_includes_the_time_range_and_calendar_name(ctx):
    commitment = _commitment()
    contact = _contact(email="andi@example.com")
    result = CreateCalendarEventPlanner().plan(commitment, contact, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    summary = result["action_plan"].summary
    assert "Meeting with Andi" in summary
    assert "Calendar: Primary" in summary


def test_plan_uses_the_commitments_own_resolved_time_never_reparsing_text(ctx):
    """A different due_at than 2pm proves the planner reads commitment.due_at
    verbatim rather than re-deriving a time from the commitment's title/text."""
    commitment = _commitment(due_at="2026-09-26T09:00:00+07:00", title="Meet Sarah", action="Meet")
    result = CreateCalendarEventPlanner().plan(commitment, None, [], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    assert result["action_plan"].payload["start_at"] == "2026-09-26T09:00:00+07:00"


def test_supporting_context_ids_only_include_calendar_event_items(ctx):
    from promise_agent.context_retrieval import ContextItem, ContextItemType, ContextSource

    commitment = _commitment()
    doc_item = ContextItem(
        id="ctx_doc", type=ContextItemType.DOCUMENT, title="doc", snippet="", score=0.5,
        source=ContextSource(system="local", source_id="doc_1"),
    )
    cal_item = ContextItem(
        id="ctx_cal", type=ContextItemType.CALENDAR_EVENT, title="evt", snippet="", score=0.5,
        source=ContextSource(system="google_calendar", source_id="evt_1"),
    )
    result = CreateCalendarEventPlanner().plan(commitment, None, [doc_item, cal_item], repos=ctx.agent_repos, agent_run_id=new_id("run"))
    assert result["action_plan"].supporting_context_ids == ["ctx_cal"]
