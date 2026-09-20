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
