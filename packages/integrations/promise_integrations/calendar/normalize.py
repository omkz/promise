from __future__ import annotations

from typing import Any

"""Normalization between Google Calendar's own Events resource shape and
PROMISE's provider-facing dict shape -- the same "never expose the raw
provider response outside the provider" rule `gmail/mime.py`'s
`normalize_gmail_message` follows. Nothing outside `calendar/provider.py`
(and its test doubles) ever sees a raw Google Calendar API response.
"""


def normalize_calendar_event(raw: dict[str, Any], *, workspace_id: str, calendar_id: str) -> dict[str, Any]:
    """Google Calendar `events.get`/`events.list` item -> PROMISE's normalized
    event dict. `start_at`/`end_at` come straight from Google's own
    `start.dateTime`/`end.dateTime` (timezone-aware, as Google returns them)
    -- an all-day event's `start.date`/`end.date` (date-only, no time) is used
    as a fallback so this never raises on one, but v1's own `create_event`
    only ever creates timed events (see that method)."""
    start = raw.get("start", {}) or {}
    end = raw.get("end", {}) or {}
    attendees = [a["email"] for a in raw.get("attendees", []) or [] if a.get("email")]
    return {
        "id": raw["id"],
        "workspace_id": workspace_id,
        "calendar_id": calendar_id,
        "summary": raw.get("summary", ""),
        "description": raw.get("description", ""),
        "location": raw.get("location"),
        "start_at": start.get("dateTime") or start.get("date") or "",
        "end_at": end.get("dateTime") or end.get("date") or "",
        "attendees": attendees,
        "status": raw.get("status", ""),
        "html_link": raw.get("htmlLink"),
        "created_at": raw.get("created", ""),
    }


def build_event_request_body(
    *, summary: str, description: str, start_at: str, end_at: str, timezone: str,
    location: str | None = None, attendees: list[str] | None = None, event_id: str | None = None,
) -> dict[str, Any]:
    """Builds the request body for `events.insert` -- timed events only
    (`start.dateTime`/`end.dateTime` with an explicit `timeZone`, never a
    naive datetime or an all-day `date`-only event; see the "TEMPORAL
    CONTEXT" requirement this satisfies). `event_id`, when given, becomes the
    request's own client-supplied `id` -- Google's documented idempotency
    mechanism for `events.insert` (a retried insert with the same `id`
    returns 409, not a duplicate event; see `GoogleCalendarIntegrationProvider.
    create_event`).
    """
    body: dict[str, Any] = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_at, "timeZone": timezone},
        "end": {"dateTime": end_at, "timeZone": timezone},
    }
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees]
    if event_id:
        body["id"] = event_id
    return body
