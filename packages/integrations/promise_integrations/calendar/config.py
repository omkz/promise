from __future__ import annotations

import os
from dataclasses import dataclass

"""Centralized Google Calendar OAuth/API configuration -- the one place
Calendar scope strings and Google's Calendar endpoint URLs are defined.
Reuses `promise_integrations.gmail.oauth.GoogleOAuthClient` (the shared
Google OAuth authorization-code-flow client -- see that module's docstring)
for the actual OAuth mechanics; only the scopes/redirect URI/API base
differ from Gmail's.

Scope (v1, deliberately minimal -- see the root README's "Google Calendar
Integration" section for why): `calendar.events.owned` -- view/create/
change/delete events on calendars owned by the authenticated user. Never the
broader `calendar` scope, and never `calendar.acls`/`calendar.calendars`/
`calendar.settings.readonly`/`calendar.freebusy` unless a concrete v1
requirement proves one necessary (none does yet).
"""

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

DEFAULT_CALENDAR_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/calendar.events.owned",
)

DEFAULT_STATE_TTL_SECONDS = 600
DEFAULT_EVENT_DURATION_MINUTES = 30


@dataclass(frozen=True)
class CalendarConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]
    state_ttl_seconds: int
    default_event_duration_minutes: int = DEFAULT_EVENT_DURATION_MINUTES


class CalendarNotConfigured(RuntimeError):
    """`CALENDAR_ENABLED=true` but one or more required environment variables
    is missing. Never raised just because Calendar is disabled -- callers
    check `calendar_enabled()` first; this is a configuration mistake, not a
    normal "not connected" state (see `promise_shared.errors.
    IntegrationNotConnected` for that)."""


def calendar_enabled() -> bool:
    return os.getenv("CALENDAR_ENABLED", "false").strip().lower() == "true"


def load_calendar_config() -> CalendarConfig:
    """Reuses `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` (the same Google Cloud
    OAuth client Gmail uses -- one Google Cloud project, two scope sets) --
    only the redirect URI is genuinely Calendar-specific, since each OAuth
    callback route is its own exact registered redirect URI with Google."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_CALENDAR_REDIRECT_URI")
    if not client_id or not client_secret or not redirect_uri:
        raise CalendarNotConfigured(
            "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_CALENDAR_REDIRECT_URI must all be set (CALENDAR_ENABLED=true)"
        )
    raw_scopes = os.getenv("CALENDAR_OAUTH_SCOPES")
    scopes = tuple(s.strip() for s in raw_scopes.split(",") if s.strip()) if raw_scopes else DEFAULT_CALENDAR_SCOPES
    return CalendarConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=scopes,
        state_ttl_seconds=int(os.getenv("GOOGLE_OAUTH_STATE_TTL", str(DEFAULT_STATE_TTL_SECONDS))),
        default_event_duration_minutes=int(
            os.getenv("CALENDAR_DEFAULT_EVENT_DURATION_MINUTES", str(DEFAULT_EVENT_DURATION_MINUTES))
        ),
    )
