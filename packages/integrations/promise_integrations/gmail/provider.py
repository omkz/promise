from __future__ import annotations

from typing import Any

import httpx
from promise_domain.enums import DraftStatus
from promise_domain.models import Document, Draft
from promise_domain.repository import Repository
from promise_shared.blobs import DocumentBlobStore
from promise_shared.clock import iso_now
from promise_shared.errors import (
    AttachmentTooLarge,
    DocumentArtifactMissing,
    IntegrationInvalidRequest,
    IntegrationPermissionDenied,
    IntegrationRateLimited,
    IntegrationUnavailable,
)
from promise_shared.secrets import SecretStore

from ..google_token_manager import GoogleTokenManager
from .config import GMAIL_API_BASE, GmailConfig
from .mime import build_raw_send_message, normalize_gmail_message

PROVIDER_NAME = "gmail"


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        raise IntegrationRateLimited(PROVIDER_NAME, retry_after=float(retry_after) if retry_after else None)
    if resp.status_code == 403:
        raise IntegrationPermissionDenied(PROVIDER_NAME)
    if resp.status_code >= 500:
        raise IntegrationUnavailable(PROVIDER_NAME, f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise IntegrationInvalidRequest(PROVIDER_NAME, f"HTTP {resp.status_code}")


class GmailIntegrationProvider:
    """Real Gmail-backed `IntegrationProvider`, bound to exactly one connected
    `IntegrationAccount`. Constructed fresh per resolved account (see
    `IntegrationRegistry.resolve_for_user`) -- never a shared, workspace-wide
    singleton the way `LocalIntegrationProvider` is, since a Gmail mailbox is
    inherently per-person, not per-workspace.

    Data minimization (v1): every call hits the Gmail API live; nothing is
    synced or persisted beyond the OAuth tokens themselves, which live in
    `secret_store` (never a normal application record -- see
    `IntegrationAccount.secret_ref`).

    v1 scope: messages only (`search_messages`/`get_message`/`send_message`).
    `search_files`/`get_file`/`create_draft` are not implemented -- Drive
    integration and Gmail's own Draft API are both explicitly out of scope for
    this milestone (PROMISE's own `Draft`/`Action` model remains the approval
    boundary; see `send_message` below, which sends the *content* of a PROMISE
    `Draft` row, never a Gmail draft).
    """

    provider_name = PROVIDER_NAME

    def __init__(
        self, *, account_id: str, workspace_id: str, secret_ref: str, secret_store: SecretStore,
        config: GmailConfig, drafts: Repository[Draft], documents: Repository[Document],
        blob_store: DocumentBlobStore, transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._account_id = account_id
        self._workspace_id = workspace_id
        self._secret_ref = secret_ref
        self._secret_store = secret_store
        self._drafts = drafts
        self._documents = documents
        self._blob_store = blob_store
        self._max_attachment_bytes = config.max_attachment_bytes
        self._attachment_encoding_margin = config.attachment_encoding_margin
        self._tokens = GoogleTokenManager(
            provider_name=PROVIDER_NAME, secret_ref=secret_ref, secret_store=secret_store, config=config, transport=transport
        )
        self._http = httpx.Client(transport=transport, timeout=10.0)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = self._tokens.access_token()
        resp = self._http.request(method, f"{GMAIL_API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if resp.status_code == 401:
            # The access token itself was rejected outright (not just "expiring soon" --
            # access_token() already handles that case). One forced refresh-and-retry,
            # never an unbounded retry loop; a second 401 after a fresh token is a real
            # failure, not a race to paper over.
            token = self._tokens.access_token(force_refresh=True)
            resp = self._http.request(method, f"{GMAIL_API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs)
        _raise_for_status(resp)
        return resp

    # ---- IntegrationProvider ------------------------------------------------------------

    def search_messages(self, workspace_id: str, query: str) -> list[dict[str, Any]]:
        resp = self._request("GET", "/users/me/messages", params={"q": query} if query else {})
        ids = [m["id"] for m in resp.json().get("messages", [])]
        messages = []
        for message_id in ids:
            message = self.get_message(workspace_id, message_id)
            if message is not None:
                messages.append(message)
        return messages

    def get_message(self, workspace_id: str, message_id: str) -> dict[str, Any] | None:
        resp = self._request("GET", f"/users/me/messages/{message_id}", params={"format": "full"})
        return normalize_gmail_message(resp.json(), workspace_id=workspace_id)

    def search_files(self, workspace_id: str, query: str) -> list[dict[str, Any]]:
        raise IntegrationInvalidRequest(PROVIDER_NAME, "file search is not supported in v1 (messages only)")

    def get_file(self, workspace_id: str, file_id: str) -> dict[str, Any] | None:
        raise IntegrationInvalidRequest(PROVIDER_NAME, "file access is not supported in v1 (messages only)")

    def create_draft(
        self, workspace_id: str, *, recipient: str, subject: str, body: str, attachment_file_id: str | None = None
    ) -> dict[str, Any]:
        raise IntegrationInvalidRequest(
            PROVIDER_NAME, "Gmail drafts are not used in v1 -- PROMISE's own Draft/Action model is the approval boundary"
        )

    def send_message(self, workspace_id: str, *, draft_id: str, idempotency_key: str) -> dict[str, Any]:
        """Sends the *content* of an existing PROMISE `Draft` row (never a Gmail
        draft -- see class docstring) via Gmail's `messages.send`.

        Idempotency: keyed off the `Draft`'s own durable `status`, not an
        in-memory set (a `GmailIntegrationProvider` is constructed fresh per
        call/resolve, so nothing in-memory here would survive between calls
        anyway) -- a `Draft` already `SENT` is treated as a replay and Gmail is
        never called again for it. This is the second guard against the
        "timed out after Gmail may have already accepted it" scenario; the
        first is `execute_action`'s own `Action.status == EXECUTED` check
        before this method is ever reached at all.

        Attachment: when the draft carries `attachment_document_id`, the
        referenced `Document` is loaded through `self._documents` -- scoped by
        `workspace_id` exactly like every other repository call, and it is
        always the *one* id the draft itself already carries (set only by
        PROMISE's own planner when the draft was created, never by a caller
        of this method) -- there is no argument surface here for reading an
        arbitrary document, so a User A draft can never end up attaching a
        User B/other-workspace document. The exact binary artifact bytes come
        from `self._blob_store` (via `Document.storage_key`) -- **never**
        `Document.content_text` (extracted text), even when a binary artifact
        exists but happens to be missing from the store: that raises
        `DocumentArtifactMissing`, never a silent fall back to text.

        Size safety (`config.max_attachment_bytes` is Gmail's own *total
        encoded message* size limit, not a raw-attachment-bytes budget -- see
        `GmailConfig`'s own docstring): two checks, both raising
        `AttachmentTooLarge` before Gmail is ever called.
          1. A fast pre-check on the attachment's raw bytes against
             `max_attachment_bytes / attachment_encoding_margin` -- conservative
             (base64 inflates content by exactly 4/3, plus MIME overhead), so it
             rejects an obviously-too-large attachment before any MIME message
             is even built.
          2. The authoritative check: once the actual base64url-encoded message
             exists, its literal length (never an estimate) is compared directly
             against `max_attachment_bytes`. This is what actually decides
             pass/fail; the pre-check is only a fast path to the same answer for
             the common case of an attachment that's obviously too big.
        """
        draft = self._drafts.require(workspace_id, draft_id)
        if draft.status == DraftStatus.SENT:
            return {"idempotent_replay": True, "draft": draft.model_dump(mode="json")}

        attachment = None
        if draft.attachment_document_id:
            document = self._documents.require(workspace_id, draft.attachment_document_id)
            if not document.storage_key:
                raise DocumentArtifactMissing(document.id)
            blob = self._blob_store.get(document.storage_key)
            if blob is None:
                raise DocumentArtifactMissing(document.id)
            conservative_raw_limit = int(self._max_attachment_bytes / self._attachment_encoding_margin)
            if blob.size_bytes > conservative_raw_limit:
                raise AttachmentTooLarge(document.id, size_bytes=blob.size_bytes, max_bytes=self._max_attachment_bytes)
            attachment = {"filename": blob.filename, "content_type": blob.content_type, "content_bytes": blob.data}

        raw = build_raw_send_message(to=draft.recipient, subject=draft.subject, body=draft.body, attachment=attachment)

        # Authoritative: base64url is pure ASCII, so len(raw) in characters is exactly
        # its byte count -- the literal size of what's about to be sent, not an estimate.
        if len(raw) > self._max_attachment_bytes:
            document_id = draft.attachment_document_id or draft.id
            raise AttachmentTooLarge(document_id, size_bytes=len(raw), max_bytes=self._max_attachment_bytes)

        resp = self._request("POST", "/users/me/messages/send", json={"raw": raw})
        sent = resp.json()

        updated = self._drafts.update(
            workspace_id, draft_id, lambda d: (setattr(d, "status", DraftStatus.SENT), setattr(d, "sent_at", iso_now()))
        )
        return {
            "idempotent_replay": False,
            "draft": updated.model_dump(mode="json"),
            "provider_message_id": sent.get("id"),
            "provider_thread_id": sent.get("threadId"),
        }
