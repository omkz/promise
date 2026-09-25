from __future__ import annotations

import time

import httpx
import pytest
from promise_integrations.calendar.config import CalendarConfig
from promise_integrations.calendar.provider import GoogleCalendarIntegrationProvider, derive_event_id
from promise_shared.errors import (
    IntegrationNotConnected,
    IntegrationNotFound,
    IntegrationPermissionDenied,
    IntegrationRateLimited,
    IntegrationUnavailable,
)

"""GoogleCalendarIntegrationProvider against httpx.MockTransport -- no live
network or real Google credentials. Mirrors tests/test_gmail_provider.py's
own pattern; uses the `ctx` fixture's real secret_store, exercising the same
GoogleTokenManager token-storage seam Gmail uses (see
packages/integrations/promise_integrations/google_token_manager.py)."""

WORKSPACE = "ws_1"
ACCOUNT_ID = "ia_cal_1"
SECRET_REF = "google_calendar:ws_1:ia_cal_1"


def _config() -> CalendarConfig:
    return CalendarConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/calendar/callback",
        scopes=("https://www.googleapis.com/auth/calendar.events.owned",), state_ttl_seconds=600,
    )


def _provider(ctx, handler) -> GoogleCalendarIntegrationProvider:
    return GoogleCalendarIntegrationProvider(
        account_id=ACCOUNT_ID, workspace_id=WORKSPACE, secret_ref=SECRET_REF, secret_store=ctx.secret_store,
        config=_config(), transport=httpx.MockTransport(handler),
    )


def _seed_valid_token(ctx, *, expires_in: float = 3600) -> None:
    ctx.secret_store.put_secret(SECRET_REF, {
        "access_token": "at_valid", "refresh_token": "rt_1", "expires_at": time.time() + expires_in,
    })


# ---- token handling (shared GoogleTokenManager, exercised through Calendar) -----------------

def test_not_connected_raises_when_no_secret_on_file(ctx):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call Calendar with no stored credentials")

    with pytest.raises(IntegrationNotConnected):
        _provider(ctx, handler).search_events(WORKSPACE)


def test_401_from_calendar_forces_one_refresh_and_retry(ctx):
    _seed_valid_token(ctx)
    call_count = {"events": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        call_count["events"] += 1
        if call_count["events"] == 1:
            return httpx.Response(401, json={})
        assert request.headers["authorization"] == "Bearer fresh"
        return httpx.Response(200, json={"items": []})

    provider = _provider(ctx, handler)
    provider.search_events(WORKSPACE)
    assert call_count["events"] == 2


# ---- search_events ----------------------------------------------------------------------------

def test_search_events_defaults_to_primary_calendar_and_normalizes(ctx):
    _seed_valid_token(ctx)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/calendar/v3/calendars/primary/events"
        assert request.url.params["singleEvents"] == "true"
        assert request.url.params["orderBy"] == "startTime"
        return httpx.Response(200, json={"items": [{
            "id": "evt_1", "summary": "Design review", "description": "quarterly",
            "start": {"dateTime": "2026-09-25T14:00:00+07:00"}, "end": {"dateTime": "2026-09-25T14:30:00+07:00"},
            "location": "HQ", "attendees": [{"email": "andi@example.com"}], "status": "confirmed",
            "htmlLink": "https://calendar.google.com/event?eid=evt_1", "created": "2026-09-20T00:00:00Z",
        }]})

    results = _provider(ctx, handler).search_events(WORKSPACE)
    assert len(results) == 1
    event = results[0]
    assert event["id"] == "evt_1"
    assert event["workspace_id"] == WORKSPACE
    assert event["calendar_id"] == "primary"
    assert event["start_at"] == "2026-09-25T14:00:00+07:00"
    assert event["end_at"] == "2026-09-25T14:30:00+07:00"
    assert event["attendees"] == ["andi@example.com"]
    assert event["html_link"] == "https://calendar.google.com/event?eid=evt_1"
    assert "payload" not in event and "start" not in event  # never the raw Google shape


def test_search_events_passes_query_and_time_window(ctx):
    _seed_valid_token(ctx)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "andi"
        assert request.url.params["timeMin"] == "2026-09-25T00:00:00+07:00"
        assert request.url.params["timeMax"] == "2026-09-26T00:00:00+07:00"
        return httpx.Response(200, json={"items": []})

    _provider(ctx, handler).search_events(
        WORKSPACE, query="andi", time_min="2026-09-25T00:00:00+07:00", time_max="2026-09-26T00:00:00+07:00",
    )


def test_search_events_paginates_through_every_page(ctx):
    _seed_valid_token(ctx)
    pages = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        pages["count"] += 1
        if pages["count"] == 1:
            assert "pageToken" not in request.url.params
            return httpx.Response(200, json={
                "items": [{"id": "evt_1", "start": {"dateTime": "2026-09-25T14:00:00+07:00"}, "end": {"dateTime": "2026-09-25T14:30:00+07:00"}}],
                "nextPageToken": "page2",
            })
        assert request.url.params["pageToken"] == "page2"
        return httpx.Response(200, json={
            "items": [{"id": "evt_2", "start": {"dateTime": "2026-09-26T14:00:00+07:00"}, "end": {"dateTime": "2026-09-26T14:30:00+07:00"}}],
        })

    results = _provider(ctx, handler).search_events(WORKSPACE)
    assert [e["id"] for e in results] == ["evt_1", "evt_2"]
    assert pages["count"] == 2


def test_search_events_on_non_primary_calendar_uses_that_calendar_id(ctx):
    _seed_valid_token(ctx)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "team@example.com" in str(request.url) or "team%40example.com" in str(request.url)
        assert "/calendars/primary/events" not in request.url.path
        return httpx.Response(200, json={"items": []})

    _provider(ctx, handler).search_events(WORKSPACE, calendar_id="team@example.com")


# ---- get_event ---------------------------------------------------------------------------------

def test_get_event_returns_none_on_404(ctx):
    _seed_valid_token(ctx)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    assert _provider(ctx, handler).get_event(WORKSPACE, "missing") is None


def test_get_event_normalizes_a_single_event(ctx):
    _seed_valid_token(ctx)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "evt_1", "summary": "1:1", "start": {"dateTime": "2026-09-25T14:00:00+07:00"},
            "end": {"dateTime": "2026-09-25T14:30:00+07:00"},
        })

    event = _provider(ctx, handler).get_event(WORKSPACE, "evt_1")
    assert event["id"] == "evt_1"
    assert event["summary"] == "1:1"


# ---- create_event -------------------------------------------------------------------------------

def test_create_event_sends_explicit_timezone_aware_start_end_and_client_id(ctx):
    _seed_valid_token(ctx)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.content)
        seen["body"] = body
        return httpx.Response(200, json={
            "id": body["id"], "summary": body["summary"], "start": body["start"], "end": body["end"],
            "htmlLink": f"https://calendar.google.com/event?eid={body['id']}", "status": "confirmed",
            "created": "2026-09-25T00:00:00Z",
        })

    result = _provider(ctx, handler).create_event(
        WORKSPACE, summary="Meeting with Andi", description="", start_at="2026-09-25T14:00:00+07:00",
        end_at="2026-09-25T14:30:00+07:00", timezone="Asia/Jakarta", idempotency_key="create_calendar_event:cmt_1",
    )
    assert seen["body"]["start"] == {"dateTime": "2026-09-25T14:00:00+07:00", "timeZone": "Asia/Jakarta"}
    assert seen["body"]["end"] == {"dateTime": "2026-09-25T14:30:00+07:00", "timeZone": "Asia/Jakarta"}
    assert seen["body"]["id"] == derive_event_id("create_calendar_event:cmt_1")
    assert result["idempotent_replay"] is False
    assert result["id"] == derive_event_id("create_calendar_event:cmt_1")


def test_create_event_never_sends_recurrence_or_reminders_fields(ctx):
    _seed_valid_token(ctx)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"id": seen["body"]["id"], "start": seen["body"]["start"], "end": seen["body"]["end"]})

    _provider(ctx, handler).create_event(
        WORKSPACE, summary="Meeting", description="", start_at="2026-09-25T14:00:00+07:00",
        end_at="2026-09-25T14:30:00+07:00", timezone="Asia/Jakarta", idempotency_key="key1",
    )
    assert "recurrence" not in seen["body"]
    assert "reminders" not in seen["body"]


def test_create_event_omits_attendees_when_none_given(ctx):
    _seed_valid_token(ctx)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"id": seen["body"]["id"], "start": seen["body"]["start"], "end": seen["body"]["end"]})

    _provider(ctx, handler).create_event(
        WORKSPACE, summary="Meeting", description="", start_at="2026-09-25T14:00:00+07:00",
        end_at="2026-09-25T14:30:00+07:00", timezone="Asia/Jakarta", idempotency_key="key1",
    )
    assert "attendees" not in seen["body"]


def test_create_event_409_conflict_is_treated_as_idempotent_replay(ctx):
    """The core idempotency mechanism: Google returns 409 for a retried insert
    with the same client-supplied id -- PROMISE fetches and returns the
    existing event instead of failing or creating a second one."""
    _seed_valid_token(ctx)
    event_id = derive_event_id("key1")
    calls = {"insert": 0, "get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["insert"] += 1
            return httpx.Response(409, json={"error": "duplicate"})
        calls["get"] += 1
        assert request.url.path == f"/calendar/v3/calendars/primary/events/{event_id}"
        return httpx.Response(200, json={
            "id": event_id, "summary": "Meeting", "start": {"dateTime": "2026-09-25T14:00:00+07:00"},
            "end": {"dateTime": "2026-09-25T14:30:00+07:00"},
        })

    result = _provider(ctx, handler).create_event(
        WORKSPACE, summary="Meeting", description="", start_at="2026-09-25T14:00:00+07:00",
        end_at="2026-09-25T14:30:00+07:00", timezone="Asia/Jakarta", idempotency_key="key1",
    )
    assert result["idempotent_replay"] is True
    assert result["id"] == event_id
    assert calls["insert"] == 1
    assert calls["get"] == 1


def test_create_event_same_idempotency_key_always_derives_the_same_event_id():
    assert derive_event_id("create_calendar_event:cmt_1") == derive_event_id("create_calendar_event:cmt_1")
    assert derive_event_id("create_calendar_event:cmt_1") != derive_event_id("create_calendar_event:cmt_2")


def test_derive_event_id_is_within_googles_required_charset_and_length():
    import re

    event_id = derive_event_id("anything at all, including spaces!")
    assert re.fullmatch(r"[a-v0-9]{5,1024}", event_id)


# ---- error classification -----------------------------------------------------------------------

def test_permission_denied_rate_limited_not_found_unavailable_classification(ctx):
    _seed_valid_token(ctx)

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    with pytest.raises(IntegrationPermissionDenied):
        _provider(ctx, forbidden).search_events(WORKSPACE)

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "5"})

    with pytest.raises(IntegrationRateLimited):
        _provider(ctx, rate_limited).search_events(WORKSPACE)

    def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    with pytest.raises(IntegrationNotFound):
        _provider(ctx, not_found).search_events(WORKSPACE)

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with pytest.raises(IntegrationUnavailable):
        _provider(ctx, unavailable).search_events(WORKSPACE)
