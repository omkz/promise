from __future__ import annotations

from typing import Any

from promise_domain.enums import DraftStatus
from promise_domain.models import Draft
from promise_domain.repository import Repository
from promise_shared.clock import iso_now
from promise_shared.errors import IntegrationInvalidRequest, IntegrationNotConnected


class MockGmailIntegrationProvider:
    """Deterministic, in-memory stand-in for `GmailIntegrationProvider`: tests and
    local development that want "Gmail is connected" behavior without any
    network access or real OAuth setup. Never contacts Gmail and never pretends
    to -- `search_messages`/`get_message` only ever return fixture messages
    seeded via `seed_messages`, and `send_message` only ever mutates the local
    `Draft` row, the same idempotency contract `GmailIntegrationProvider.
    send_message` has (keyed off `Draft.status`, not an in-memory set).
    """

    provider_name = "gmail"

    def __init__(self, drafts: Repository[Draft], *, connected: bool = True) -> None:
        self._drafts = drafts
        self._connected = connected
        self._messages: dict[str, dict[str, Any]] = {}
        self.sent_draft_ids: list[str] = []

    def seed_messages(self, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            self._messages[message["id"]] = message

    def _require_connected(self) -> None:
        if not self._connected:
            raise IntegrationNotConnected(self.provider_name)

    def search_messages(self, workspace_id: str, query: str) -> list[dict[str, Any]]:
        self._require_connected()
        q = query.lower().strip()
        rows = [m for m in self._messages.values() if m.get("workspace_id") == workspace_id]
        if not q:
            return rows
        return [m for m in rows if q in " ".join(str(v) for v in m.values()).lower()]

    def get_message(self, workspace_id: str, message_id: str) -> dict[str, Any] | None:
        self._require_connected()
        message = self._messages.get(message_id)
        if message is None or message.get("workspace_id") != workspace_id:
            return None
        return message

    def search_files(self, workspace_id: str, query: str) -> list[dict[str, Any]]:
        raise IntegrationInvalidRequest(self.provider_name, "file search is not supported in v1 (messages only)")

    def get_file(self, workspace_id: str, file_id: str) -> dict[str, Any] | None:
        raise IntegrationInvalidRequest(self.provider_name, "file access is not supported in v1 (messages only)")

    def create_draft(
        self, workspace_id: str, *, recipient: str, subject: str, body: str, attachment_file_id: str | None = None
    ) -> dict[str, Any]:
        raise IntegrationInvalidRequest(self.provider_name, "Gmail drafts are not used in v1")

    def send_message(self, workspace_id: str, *, draft_id: str, idempotency_key: str) -> dict[str, Any]:
        self._require_connected()
        draft = self._drafts.require(workspace_id, draft_id)
        if draft.status == DraftStatus.SENT:
            return {"idempotent_replay": True, "draft": draft.model_dump(mode="json")}
        updated = self._drafts.update(
            workspace_id, draft_id, lambda d: (setattr(d, "status", DraftStatus.SENT), setattr(d, "sent_at", iso_now()))
        )
        self.sent_draft_ids.append(draft_id)
        return {
            "idempotent_replay": False,
            "draft": updated.model_dump(mode="json"),
            "provider_message_id": f"mock_gmail_msg_{draft_id}",
            "provider_thread_id": f"mock_gmail_thread_{draft_id}",
        }
