from __future__ import annotations

import time
from typing import Any

import httpx
from promise_domain.enums import DraftStatus
from promise_domain.models import Draft
from promise_domain.repository import Repository
from promise_shared.clock import iso_now
from promise_shared.errors import (
    IntegrationAuthorizationRevoked,
    IntegrationInvalidRequest,
    IntegrationNotConnected,
    IntegrationPermissionDenied,
    IntegrationRateLimited,
    IntegrationTokenExpired,
    IntegrationUnavailable,
)
from promise_shared.secrets import SecretStore

from .config import GMAIL_API_BASE, GmailConfig
from .mime import build_raw_send_message, normalize_gmail_message
from .oauth import GoogleOAuthClient

PROVIDER_NAME = "gmail"

# Refresh a bit before actual expiry so a call never races Google's own clock.
_TOKEN_REFRESH_SKEW_SECONDS = 60


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
        config: GmailConfig, drafts: Repository[Draft], transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._account_id = account_id
        self._workspace_id = workspace_id
        self._secret_ref = secret_ref
        self._secret_store = secret_store
        self._drafts = drafts
        self._oauth = GoogleOAuthClient(config, transport=transport)
        self._http = httpx.Client(transport=transport, timeout=10.0)

    # ---- token handling -----------------------------------------------------------------

    def _refresh(self, refresh_token: str) -> dict[str, Any]:
        try:
            refreshed = self._oauth.refresh_access_token(refresh_token)
        except IntegrationAuthorizationRevoked:
            # Authorization was revoked outside PROMISE (e.g. the user removed access from
            # their Google Account) -- never retry the same refresh token again; the account
            # must be reconnected. Deleting the secret here means every subsequent call fails
            # closed with IntegrationNotConnected/IntegrationTokenExpired, not a repeated
            # revoked-token error against Google.
            self._secret_store.delete_secret(self._secret_ref)
            raise
        self._secret_store.put_secret(self._secret_ref, refreshed)
        return refreshed

    def _access_token(self, *, force_refresh: bool = False) -> str:
        secret = self._secret_store.get_secret(self._secret_ref)
        if secret is None:
            raise IntegrationNotConnected(PROVIDER_NAME)
        if not force_refresh and secret.get("expires_at", 0) > time.time() + _TOKEN_REFRESH_SKEW_SECONDS:
            return secret["access_token"]
        refresh_token = secret.get("refresh_token")
        if not refresh_token:
            raise IntegrationTokenExpired(PROVIDER_NAME)
        return self._refresh(refresh_token)["access_token"]

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = self._access_token()
        resp = self._http.request(method, f"{GMAIL_API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if resp.status_code == 401:
            # The access token itself was rejected outright (not just "expiring soon" --
            # _access_token() already handles that case). One forced refresh-and-retry,
            # never an unbounded retry loop; a second 401 after a fresh token is a real
            # failure, not a race to paper over.
            token = self._access_token(force_refresh=True)
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
        """
        draft = self._drafts.require(workspace_id, draft_id)
        if draft.status == DraftStatus.SENT:
            return {"idempotent_replay": True, "draft": draft.model_dump(mode="json")}

        raw = build_raw_send_message(to=draft.recipient, subject=draft.subject, body=draft.body)
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
