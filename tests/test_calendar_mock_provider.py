from __future__ import annotations

import pytest
from promise_integrations.calendar.mock_provider import MockGoogleCalendarProvider
from promise_integrations.calendar.provider import derive_event_id
from promise_shared.errors import IntegrationNotConnected

"""MockGoogleCalendarProvider -- deterministic, in-memory, never contacts
Google. Uses the exact same derive_event_id idempotency mechanism as
GoogleCalendarIntegrationProvider (see tests/test_calendar_provider.py), so
tests built on the mock exercise the real idempotency logic."""

WORKSPACE = "ws_1"


def test_search_events_returns_only_seeded_events_for_the_workspace():
    mock = MockGoogleCalendarProvider()
    mock.seed_events([
        {"id": "evt_1", "workspace_id": WORKSPACE, "calendar_id": "primary", "summary": "Design review with Andi",
         "start_at": "2026-09-25T14:00:00+07:00", "end_at": "2026-09-25T14:30:00+07:00"},
        {"id": "evt_2", "workspace_id": "ws_other", "calendar_id": "primary", "summary": "Someone else's event",
         "start_at": "2026-09-25T15:00:00+07:00", "end_at": "2026-09-25T15:30:00+07:00"},
    ])
    results = mock.search_events(WORKSPACE)
    assert [e["id"] for e in results] == ["evt_1"]


def test_search_events_filters_by_query():
    mock = MockGoogleCalendarProvider()
    mock.seed_events([
        {"id": "evt_1", "workspace_id": WORKSPACE, "summary": "Design review with Andi",
         "start_at": "2026-09-25T14:00:00+07:00", "end_at": "2026-09-25T14:30:00+07:00"},
        {"id": "evt_2", "workspace_id": WORKSPACE, "summary": "1:1 with Sarah",
         "start_at": "2026-09-25T15:00:00+07:00", "end_at": "2026-09-25T15:30:00+07:00"},
    ])
    results = mock.search_events(WORKSPACE, query="andi")
    assert [e["id"] for e in results] == ["evt_1"]


def test_search_events_filters_by_time_window():
    mock = MockGoogleCalendarProvider()
    mock.seed_events([
        {"id": "evt_fri", "workspace_id": WORKSPACE, "summary": "Friday event",
         "start_at": "2026-09-25T14:00:00+07:00", "end_at": "2026-09-25T14:30:00+07:00"},
        {"id": "evt_next_week", "workspace_id": WORKSPACE, "summary": "Next week event",
         "start_at": "2026-10-02T14:00:00+07:00", "end_at": "2026-10-02T14:30:00+07:00"},
    ])
    results = mock.search_events(WORKSPACE, time_min="2026-09-25T00:00:00+07:00", time_max="2026-09-26T00:00:00+07:00")
    assert [e["id"] for e in results] == ["evt_fri"]


def test_get_event_returns_none_for_unknown_or_other_workspace():
    mock = MockGoogleCalendarProvider()
    mock.seed_events([{"id": "evt_1", "workspace_id": "ws_other", "start_at": "x", "end_at": "y"}])
    assert mock.get_event(WORKSPACE, "evt_1") is None
    assert mock.get_event(WORKSPACE, "does-not-exist") is None


def test_create_event_is_idempotent_on_idempotency_key():
    mock = MockGoogleCalendarProvider()
    first = mock.create_event(
        WORKSPACE, summary="Meeting with Andi", start_at="2026-09-25T14:00:00+07:00", end_at="2026-09-25T14:30:00+07:00",
        timezone="Asia/Jakarta", idempotency_key="key1",
    )
    second = mock.create_event(
        WORKSPACE, summary="Meeting with Andi", start_at="2026-09-25T14:00:00+07:00", end_at="2026-09-25T14:30:00+07:00",
        timezone="Asia/Jakarta", idempotency_key="key1",
    )
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert first["id"] == second["id"] == derive_event_id("key1")
    assert mock.created_event_ids == [derive_event_id("key1")]  # created exactly once


def test_create_event_raises_when_not_connected():
    mock = MockGoogleCalendarProvider(connected=False)
    with pytest.raises(IntegrationNotConnected):
        mock.create_event(
            WORKSPACE, summary="x", start_at="2026-09-25T14:00:00+07:00", end_at="2026-09-25T14:30:00+07:00",
            timezone="Asia/Jakarta", idempotency_key="key1",
        )


def test_search_events_raises_when_not_connected():
    mock = MockGoogleCalendarProvider(connected=False)
    with pytest.raises(IntegrationNotConnected):
        mock.search_events(WORKSPACE)
