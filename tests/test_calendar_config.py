from __future__ import annotations

import pytest
from promise_integrations.calendar.config import (
    DEFAULT_CALENDAR_SCOPES,
    DEFAULT_EVENT_DURATION_MINUTES,
    CalendarConfig,
    CalendarNotConfigured,
    calendar_enabled,
    load_calendar_config,
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


def test_default_scope_is_the_narrow_events_owned_scope():
    assert DEFAULT_CALENDAR_SCOPES == ("https://www.googleapis.com/auth/calendar.events.owned",)


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


def test_calendar_config_is_constructible_directly():
    config = CalendarConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/calendar/callback",
        scopes=DEFAULT_CALENDAR_SCOPES, state_ttl_seconds=600,
    )
    assert config.default_event_duration_minutes == DEFAULT_EVENT_DURATION_MINUTES
