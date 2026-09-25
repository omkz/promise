from __future__ import annotations

from typing import Any, Protocol


class IntegrationProvider(Protocol):
    """Everything the agent needs from an external system (email, drive, chat, ...).

    The agent/domain layer never imports Gmail/Drive/Slack SDKs directly —
    it only ever talks to this Protocol. Adding Gmail later means adding a
    `GmailIntegrationProvider` that implements this interface; nothing in
    `promise_agent` or `promise_app` has to change.
    """

    provider_name: str

    def search_messages(self, workspace_id: str, query: str) -> list[dict[str, Any]]: ...

    def get_message(self, workspace_id: str, message_id: str) -> dict[str, Any] | None: ...

    def search_files(self, workspace_id: str, query: str) -> list[dict[str, Any]]: ...

    def get_file(self, workspace_id: str, file_id: str) -> dict[str, Any] | None: ...

    def create_draft(
        self, workspace_id: str, *, recipient: str, subject: str, body: str, attachment_file_id: str | None = None
    ) -> dict[str, Any]: ...

    def send_message(self, workspace_id: str, *, draft_id: str, idempotency_key: str) -> dict[str, Any]:
        """Perform the real external side effect. Must be idempotent on idempotency_key."""
        ...


class CalendarProvider(Protocol):
    """Everything the agent needs from an external calendar system.

    A sibling abstraction to `IntegrationProvider`, not an implementation of
    it: `IntegrationProvider`'s methods are message/file-shaped
    (`search_messages`, `create_draft`, ...) and have no calendar-shaped
    equivalent, so calendar support is its own small Protocol rather than
    stretching the existing one to fit. Both are resolved the same way --
    per-user, via `IntegrationRegistry.resolve_for_user(..., provider_name=...)`
    -- and follow the same rules (never execute a real side effect outside
    the approval flow; `create_event` must be idempotent on `idempotency_key`,
    exactly like `IntegrationProvider.send_message`).

    v1 is intentionally read + create only. `update_event`/`delete_event`/
    `move_event` are named here as the natural extension points for later,
    not implemented — see `GoogleCalendarIntegrationProvider`'s own docstring.
    """

    provider_name: str

    def search_events(
        self, workspace_id: str, *, query: str = "", time_min: str | None = None, time_max: str | None = None,
        calendar_id: str = "primary",
    ) -> list[dict[str, Any]]: ...

    def get_event(self, workspace_id: str, event_id: str, *, calendar_id: str = "primary") -> dict[str, Any] | None: ...

    def create_event(
        self, workspace_id: str, *, calendar_id: str = "primary", summary: str, description: str = "",
        start_at: str, end_at: str, timezone: str, location: str | None = None, attendees: list[str] | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Perform the real external side effect. Must be idempotent on idempotency_key.
        `start_at`/`end_at` must already be explicit, timezone-aware ISO 8601 timestamps
        (never a naive datetime), with `timezone` naming their IANA zone explicitly --
        never invented by an implementation."""
        ...
