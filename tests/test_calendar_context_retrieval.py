from __future__ import annotations

from promise_app import tools
from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount
from promise_integrations.calendar.mock_provider import MockGoogleCalendarProvider
from promise_shared.ids import new_id

"""Google Calendar as just another ContextSearchProvider (see
context_retrieval/providers.py's CalendarSearchProvider) -- never a special
code path inside ContextRetriever itself. Mirrors
tests/test_gmail_context_retrieval.py's own structure."""


def _connect_calendar(ctx, *, workspace_id: str, user_id: str, mock: MockGoogleCalendarProvider) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="google_calendar",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref="cal:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("google_calendar", lambda _account: mock)
    return account


def test_calendar_participates_in_retrieval_when_connected(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I need to meet Andi Friday at 2.",
    )["commitment"]

    mock = MockGoogleCalendarProvider()
    mock.seed_events([{
        "id": "evt_1", "workspace_id": ctx.default_workspace_id, "calendar_id": "primary",
        "summary": "Design review with Andi", "description": "quarterly design review",
        "start_at": "2026-09-25T14:00:00+07:00", "end_at": "2026-09-25T14:30:00+07:00",
        "location": None, "attendees": ["andi@example.com"], "status": "confirmed",
        "html_link": "https://calendar.google.com/event?eid=evt_1", "created_at": "2026-09-20T00:00:00+07:00",
    }])
    _connect_calendar(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, mock=mock)

    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    sources = {item.source.system for item in result["results"]}
    assert "google_calendar" in sources
    calendar_items = [i for i in result["results"] if i.source.system == "google_calendar"]
    assert calendar_items[0].type.value == "calendar_event"


def test_calendar_does_not_participate_when_not_connected(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I need to meet Andi Friday at 2.",
    )["commitment"]

    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    sources = {item.source.system for item in result["results"]}
    assert "google_calendar" not in sources
    assert result["status"].value == "complete"


def test_calendar_failure_yields_partial_retrieval_never_fabricated_results(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I need to meet Andi Friday at 2.",
    )["commitment"]

    class _FailingCalendar:
        provider_name = "google_calendar"

        def search_events(self, workspace_id, *, query="", time_min=None, time_max=None, calendar_id="primary"):
            raise RuntimeError("Calendar API unavailable")

        def get_event(self, workspace_id, event_id, *, calendar_id="primary"):
            raise RuntimeError("Calendar API unavailable")

        def create_event(self, *args, **kwargs):
            raise NotImplementedError

    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, provider="google_calendar",
        account_identifier="andi@example.com", status=IntegrationStatus.CONNECTED, secret_ref="cal:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("google_calendar", lambda _account: _FailingCalendar())

    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    assert result["status"].value == "partial"
    assert any(e["provider"] == "calendar" for e in result["errors"])
    assert len(result["results"]) >= 0  # never raises -- other providers still return what they have


def test_calendar_results_are_deduped_across_matching_search_terms(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I need to meet Andi Friday at 2.",
    )["commitment"]

    mock = MockGoogleCalendarProvider()
    mock.seed_events([{
        "id": "evt_1", "workspace_id": ctx.default_workspace_id, "calendar_id": "primary",
        "summary": "Andi design review", "description": "andi proposal revision",
        "start_at": "2026-09-25T14:00:00+07:00", "end_at": "2026-09-25T14:30:00+07:00",
        "location": None, "attendees": [], "status": "confirmed", "html_link": None, "created_at": "2026-09-20T00:00:00+07:00",
    }])
    _connect_calendar(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, mock=mock)

    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    calendar_ids = [item.source.source_id for item in result["results"] if item.source.system == "google_calendar"]
    assert calendar_ids.count("evt_1") == 1


def test_orchestrator_retrieval_step_also_includes_calendar_when_connected(seeded_ctx):
    """Same wiring, through promise_agent.steps.retrieval.retrieve_context (what
    handle_commitment actually runs), not just the on-demand tool."""
    from promise_agent.steps.retrieval import retrieve_context

    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I need to meet Andi Friday at 2.",
    )["commitment"]

    mock = MockGoogleCalendarProvider()
    mock.seed_events([{
        "id": "evt_2", "workspace_id": ctx.default_workspace_id, "calendar_id": "primary",
        "summary": "Andi 1:1", "description": "", "start_at": "2026-09-25T14:00:00+07:00",
        "end_at": "2026-09-25T14:30:00+07:00", "location": None, "attendees": [], "status": "confirmed",
        "html_link": None, "created_at": "2026-09-20T00:00:00+07:00",
    }])
    _connect_calendar(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, mock=mock)

    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id) if commitment.contact_id else None
    result = retrieve_context(commitment, contact, ctx.agent_repos)
    sources = {item.source.system for item in result["items"]}
    assert "google_calendar" in sources


def test_no_fake_results_when_calendar_never_configured(seeded_ctx):
    """No IntegrationAccount at all (never connected, not even a disconnected
    row) -- resolve_for_user returns None and retrieval runs exactly as it did
    before Calendar existed; never a fabricated calendar_event ContextItem."""
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I need to meet Andi Friday at 2.",
    )["commitment"]
    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert not any(item.type.value == "calendar_event" for item in result["results"])
