from __future__ import annotations

from typing import Any

from promise_domain.enums import DraftStatus
from promise_domain.models import Draft
from promise_domain.repository import Repository
from promise_shared.clock import iso_now
from promise_shared.ids import new_id


class LocalIntegrationProvider:
    """Local/demo integration provider.

    Implements the same `IntegrationProvider` shape a real Gmail/Drive
    provider would, but reads/writes workspace documents, messages, and
    drafts already stored in PROMISE instead of calling an external API.
    Used for local development, tests, and the seeded demo workspace.

    `send_message` here performs a *real* mutation of local state (marks the
    draft sent) so tests can assert on it, but never talks to a real mail
    server — it must never be mistaken for a live send.
    """

    provider_name = "local"

    def __init__(
        self,
        documents: Repository,
        messages: Repository,
        drafts: Repository,
        *,
        _sent_idempotency_keys: set[str] | None = None,
    ) -> None:
        self._documents = documents
        self._messages = messages
        self._drafts = drafts
        self._sent_idempotency_keys = _sent_idempotency_keys if _sent_idempotency_keys is not None else set()

    def search_messages(self, workspace_id: str, query: str) -> list[dict[str, Any]]:
        q = query.lower().strip()
        rows = self._messages.list(workspace_id)
        if not q:
            return [r.model_dump(mode="json") for r in rows]
        return [r.model_dump(mode="json") for r in rows if q in _searchable_text(r.model_dump(mode="json"))]

    def get_message(self, workspace_id: str, message_id: str) -> dict[str, Any] | None:
        row = self._messages.get(workspace_id, message_id)
        return row.model_dump(mode="json") if row else None

    def search_files(self, workspace_id: str, query: str) -> list[dict[str, Any]]:
        q = query.lower().strip()
        rows = self._documents.list(workspace_id)
        if not q:
            return [r.model_dump(mode="json") for r in rows]
        return [r.model_dump(mode="json") for r in rows if q in _searchable_text(r.model_dump(mode="json"))]

    def get_file(self, workspace_id: str, file_id: str) -> dict[str, Any] | None:
        row = self._documents.get(workspace_id, file_id)
        return row.model_dump(mode="json") if row else None

    def create_draft(
        self, workspace_id: str, *, recipient: str, subject: str, body: str, attachment_file_id: str | None = None
    ) -> dict[str, Any]:
        draft = Draft(
            id=new_id("draft"),
            workspace_id=workspace_id,
            recipient=recipient,
            subject=subject,
            body=body,
            attachment_document_id=attachment_file_id,
        )
        self._drafts.save(draft)
        return draft.model_dump(mode="json")

    def send_message(self, workspace_id: str, *, draft_id: str, idempotency_key: str) -> dict[str, Any]:
        if idempotency_key in self._sent_idempotency_keys:
            draft = self._drafts.require(workspace_id, draft_id)
            return {"idempotent_replay": True, "draft": draft.model_dump(mode="json")}
        draft = self._drafts.update(
            workspace_id,
            draft_id,
            lambda d: (setattr(d, "status", DraftStatus.SENT), setattr(d, "sent_at", iso_now())),
        )
        self._sent_idempotency_keys.add(idempotency_key)
        return {"idempotent_replay": False, "draft": draft.model_dump(mode="json")}


def _searchable_text(row: dict[str, Any]) -> str:
    return " ".join(str(v) for v in row.values()).lower()
