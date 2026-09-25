from __future__ import annotations

from typing import Any

from promise_shared.errors import IntegrationNotConnected

from .provider import derive_event_id


class MockGoogleCalendarProvider:
    """Deterministic, in-memory stand-in for `GoogleCalendarIntegrationProvider`:
    tests and local development that want "Calendar is connected" behavior
    without any network access or real OAuth setup. Never contacts Google --
    `search_events`/`get_event` only ever return fixture events seeded via
    `seed_events`, and `create_event` only ever mutates an in-memory dict,
    using the exact same `derive_event_id` idempotency mechanism the real
    provider does (so tests exercise the real idempotency *logic*, not a
    simplified stand-in for it).
    """

    provider_name = "google_calendar"

    def __init__(self, *, connected: bool = True) -> None:
        self._connected = connected
        self._events: dict[str, dict[str, Any]] = {}
        self.created_event_ids: list[str] = []

    def seed_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            self._events[event["id"]] = event

    def _require_connected(self) -> None:
        if not self._connected:
            raise IntegrationNotConnected(self.provider_name)

    def search_events(
        self, workspace_id: str, *, query: str = "", time_min: str | None = None, time_max: str | None = None,
        calendar_id: str = "primary",
    ) -> list[dict[str, Any]]:
        self._require_connected()
        rows = [e for e in self._events.values() if e.get("workspace_id") == workspace_id and e.get("calendar_id", "primary") == calendar_id]
        if time_min:
            rows = [e for e in rows if e.get("start_at", "") >= time_min]
        if time_max:
            rows = [e for e in rows if e.get("start_at", "") <= time_max]
        if query:
            q = query.lower()
            rows = [e for e in rows if q in " ".join(str(v) for v in e.values()).lower()]
        return sorted(rows, key=lambda e: e.get("start_at", ""))

    def get_event(self, workspace_id: str, event_id: str, *, calendar_id: str = "primary") -> dict[str, Any] | None:
        self._require_connected()
        event = self._events.get(event_id)
        if event is None or event.get("workspace_id") != workspace_id or event.get("calendar_id", "primary") != calendar_id:
            return None
        return event

    def create_event(
        self, workspace_id: str, *, calendar_id: str = "primary", summary: str, description: str = "",
        start_at: str, end_at: str, timezone: str, location: str | None = None, attendees: list[str] | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_connected()
        event_id = derive_event_id(idempotency_key)
        existing = self._events.get(event_id)
        if existing is not None:
            return {**existing, "idempotent_replay": True}

        event = {
            "id": event_id, "workspace_id": workspace_id, "calendar_id": calendar_id, "summary": summary,
            "description": description, "location": location, "start_at": start_at, "end_at": end_at,
            "attendees": attendees or [], "status": "confirmed",
            "html_link": f"https://calendar.google.com/event?eid=mock_{event_id}",
            "created_at": start_at,
        }
        self._events[event_id] = event
        self.created_event_ids.append(event_id)
        return {**event, "idempotent_replay": False}
