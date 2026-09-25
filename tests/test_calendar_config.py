from __future__ import annotations

import pytest
from promise_integrations.calendar.config import (
    DEFAULT_CALENDAR_SCOPES,
    DEFAULT_EVENT_DURATION_MINUTES,
    CalendarConfig,
    CalendarNotConfigured,
    calendar_enabled,
    load_calendar_config,
    load_default_event_duration_minutes,
)

"""CalendarConfig / calendar_enabled / load_calendar_config -- centralized
Calendar OAuth/API configuration, deliberately reusing the same
GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET Gmail already reads (one Google Cloud
OAuth client, two scope sets), with only the redirect URI genuinely
Calendar-specific."""

ENV = {
    "GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csecret",
    "GOOGLE_CALENDAR_REDIRECT_URI": "https://promise.example/calendar/callback",
}


def _set_env(monkeypatch, **overrides):
    for key, value in {**ENV, **overrides}.items():
        monkeypatch.setenv(key, value)


def test_default_scope_is_the_narrow_events_owned_scope_plus_minimal_identity_scopes():
    """`calendar.events.owned` for the actual Calendar access, plus `openid` +
    `userinfo.email` -- the minimal identity scopes `GoogleOAuthClient.get_identity`
    needs to resolve which Google account connected -- and nothing else."""
    assert DEFAULT_CALENDAR_SCOPES == (
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/calendar.events.owned",
    )


def test_default_scope_never_requests_a_gmail_scope():
    assert not any("gmail" in scope for scope in DEFAULT_CALENDAR_SCOPES)


def test_default_scope_excludes_broad_and_unnecessary_scopes():
    joined = " ".join(DEFAULT_CALENDAR_SCOPES)
    for forbidden in (
        "https://www.googleapis.com/auth/calendar ",
        "calendar.acls", "calendar.calendars", "calendar.settings.readonly", "calendar.freebusy",
    ):
        assert forbidden not in joined


def test_calendar_enabled_defaults_false(monkeypatch):
    monkeypatch.delenv("CALENDAR_ENABLED", raising=False)
    assert calendar_enabled() is False


def test_calendar_enabled_true(monkeypatch):
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    assert calendar_enabled() is True


def test_load_calendar_config_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CALENDAR_REDIRECT_URI", raising=False)
    with pytest.raises(CalendarNotConfigured):
        load_calendar_config()


def test_load_calendar_config_defaults(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.delenv("CALENDAR_OAUTH_SCOPES", raising=False)
    monkeypatch.delenv("CALENDAR_DEFAULT_EVENT_DURATION_MINUTES", raising=False)
    config = load_calendar_config()
    assert config.client_id == "cid"
    assert config.redirect_uri == "https://promise.example/calendar/callback"
    assert config.scopes == DEFAULT_CALENDAR_SCOPES
    assert config.default_event_duration_minutes == DEFAULT_EVENT_DURATION_MINUTES


def test_load_calendar_config_scope_env_override(monkeypatch):
    _set_env(monkeypatch, CALENDAR_OAUTH_SCOPES="https://www.googleapis.com/auth/calendar.events.owned,https://www.googleapis.com/auth/calendar.readonly")
    config = load_calendar_config()
    assert len(config.scopes) == 2


def test_load_calendar_config_uses_its_own_redirect_uri_not_gmails(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://promise.example/gmail/callback")
    config = load_calendar_config()
    assert config.redirect_uri == "https://promise.example/calendar/callback"


def test_load_default_event_duration_minutes_default(monkeypatch):
    monkeypatch.delenv("CALENDAR_DEFAULT_EVENT_DURATION_MINUTES", raising=False)
    assert load_default_event_duration_minutes() == DEFAULT_EVENT_DURATION_MINUTES


def test_load_default_event_duration_minutes_override_requires_no_oauth_credentials(monkeypatch):
    """Unlike `load_calendar_config`, this never raises `CalendarNotConfigured` --
    `CreateCalendarEventPlanner` reads the configured duration even when Calendar
    OAuth itself isn't configured (planning never depends on live credentials)."""
    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_CALENDAR_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CALENDAR_DEFAULT_EVENT_DURATION_MINUTES", "45")
    assert load_default_event_duration_minutes() == 45


def test_calendar_config_is_constructible_directly():
    config = CalendarConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/calendar/callback",
        scopes=DEFAULT_CALENDAR_SCOPES, state_ttl_seconds=600,
    )
    assert config.default_event_duration_minutes == DEFAULT_EVENT_DURATION_MINUTES
