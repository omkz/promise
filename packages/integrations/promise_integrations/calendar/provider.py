from __future__ import annotations

import hashlib
from typing import Any

import httpx
from promise_shared.errors import (
    IntegrationConflict,
    IntegrationInvalidRequest,
    IntegrationNotFound,
    IntegrationPermissionDenied,
    IntegrationRateLimited,
    IntegrationUnavailable,
)
from promise_shared.secrets import SecretStore

from ..google_token_manager import GoogleTokenManager
from .config import CALENDAR_API_BASE, CalendarConfig
from .normalize import build_event_request_body, normalize_calendar_event

PROVIDER_NAME = "google_calendar"


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        raise IntegrationRateLimited(PROVIDER_NAME, retry_after=float(retry_after) if retry_after else None)
    if resp.status_code == 403:
        raise IntegrationPermissionDenied(PROVIDER_NAME)
    if resp.status_code == 404:
        raise IntegrationNotFound(PROVIDER_NAME, "not found")
    if resp.status_code >= 500:
        raise IntegrationUnavailable(PROVIDER_NAME, f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise IntegrationInvalidRequest(PROVIDER_NAME, f"HTTP {resp.status_code}")


def derive_event_id(idempotency_key: str) -> str:
    """Google Calendar's client-supplied event `id` must match `^[a-v0-9]{5,1024}$`
    (lowercase base32hex). A SHA-1 hex digest is already a subset of that
    alphabet (hex uses `0-9a-f`, all within `a-v`) and always 40 characters
    (within the 5-1024 range), so it's used directly -- no extra encoding.
    Deterministic: the same `idempotency_key` (`Action.idempotency_key`)
    always derives the same event id, which is the entire idempotency
    mechanism -- see `GoogleCalendarIntegrationProvider.create_event`.
    """
    return hashlib.sha1(idempotency_key.encode("utf-8")).hexdigest()


class GoogleCalendarIntegrationProvider:
    """Real Google Calendar-backed `CalendarProvider`, bound to exactly one
    connected `IntegrationAccount`. Constructed fresh per resolved account
    (see `IntegrationRegistry.resolve_for_user`) -- never a shared,
    workspace-wide singleton, the same reasoning `GmailIntegrationProvider`
    follows (a calendar is inherently per-person, not per-workspace).

    v1 scope: `search_events`/`get_event`/`create_event` only.
    `update_event`/`delete_event`/`move_event` are natural extension points
    (see `CalendarProvider`'s own docstring) but not implemented -- v1 never
    edits or removes a calendar event it didn't just create.
    """

    provider_name = PROVIDER_NAME

    def __init__(
        self, *, account_id: str, workspace_id: str, secret_ref: str, secret_store: SecretStore,
        config: CalendarConfig, transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._account_id = account_id
        self._workspace_id = workspace_id
        self._secret_ref = secret_ref
        self._tokens = GoogleTokenManager(
            provider_name=PROVIDER_NAME, secret_ref=secret_ref, secret_store=secret_store, config=config, transport=transport
        )
        self._http = httpx.Client(transport=transport, timeout=10.0)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = self._tokens.access_token()
        resp = self._http.request(method, f"{CALENDAR_API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if resp.status_code == 401:
            # One forced refresh-and-retry, never an unbounded loop -- same pattern
            # GmailIntegrationProvider uses.
            token = self._tokens.access_token(force_refresh=True)
            resp = self._http.request(method, f"{CALENDAR_API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs)
        return resp

    def search_events(
        self, workspace_id: str, *, query: str = "", time_min: str | None = None, time_max: str | None = None,
        calendar_id: str = "primary",
    ) -> list[dict[str, Any]]:
        """Uses the Calendar Events `list` API with `singleEvents=true` (recurring
        events expanded to concrete instances -- v1 has no notion of a
        recurring series) and `orderBy=startTime`. `time_min`/`time_max` must
        already be explicit, timezone-aware ISO 8601 strings (RFC 3339,
        Google's required format) -- this method never invents or normalizes
        a bare/naive datetime itself; see `promise_agent.commitment_extraction.
        temporal` for where that normalization happens. Paginates through
        every page via `nextPageToken` -- callers get the complete result set
        for the given window, not just the first page.
        """
        params: dict[str, Any] = {"singleEvents": "true", "orderBy": "startTime", "maxResults": 50}
        if query:
            params["q"] = query
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max

        events: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            page_params = dict(params)
            if page_token:
                page_params["pageToken"] = page_token
            resp = self._request("GET", f"/calendars/{calendar_id}/events", params=page_params)
            _raise_for_status(resp)
            body = resp.json()
            for raw in body.get("items", []):
                events.append(normalize_calendar_event(raw, workspace_id=workspace_id, calendar_id=calendar_id))
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        return events

    def get_event(self, workspace_id: str, event_id: str, *, calendar_id: str = "primary") -> dict[str, Any] | None:
        resp = self._request("GET", f"/calendars/{calendar_id}/events/{event_id}")
        if resp.status_code == 404:
            return None
        _raise_for_status(resp)
        return normalize_calendar_event(resp.json(), workspace_id=workspace_id, calendar_id=calendar_id)

    def create_event(
        self, workspace_id: str, *, calendar_id: str = "primary", summary: str, description: str = "",
        start_at: str, end_at: str, timezone: str, location: str | None = None, attendees: list[str] | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Creates a real Google Calendar event via `events.insert` -- called only
        from the approval-gated execution step (`execute_action`), never during
        planning (see `CreateCalendarEventPlanner`).

        Idempotency: uses Google's own documented mechanism for
        `events.insert` -- a client-supplied event `id` (derived
        deterministically from `idempotency_key`, see `derive_event_id`).
        Retrying `create_event` with the same `idempotency_key` always
        targets the same event id; Google returns `409 Conflict` for a
        duplicate insert rather than creating a second event, and that 409 is
        treated here as a replay (the existing event is fetched and returned
        with `idempotent_replay: True`), never a hard failure. This is what
        prevents a duplicate event after a network timeout where PROMISE
        doesn't know whether the first `events.insert` actually succeeded.
        `start_at`/`end_at` must already be explicit, timezone-aware ISO 8601
        timestamps with `timezone` naming their zone -- see `search_events`'s
        docstring; this method never sends Google a naive datetime.
        """
        event_id = derive_event_id(idempotency_key)
        body = build_event_request_body(
            summary=summary, description=description, start_at=start_at, end_at=end_at, timezone=timezone,
            location=location, attendees=attendees, event_id=event_id,
        )
        resp = self._request("POST", f"/calendars/{calendar_id}/events", json=body)
        if resp.status_code == 409:
            existing = self.get_event(workspace_id, event_id, calendar_id=calendar_id)
            if existing is not None:
                return {**existing, "idempotent_replay": True}
            raise IntegrationConflict(PROVIDER_NAME, "event id conflict but the existing event could not be re-fetched")
        _raise_for_status(resp)
        created = normalize_calendar_event(resp.json(), workspace_id=workspace_id, calendar_id=calendar_id)
        return {**created, "idempotent_replay": False}
