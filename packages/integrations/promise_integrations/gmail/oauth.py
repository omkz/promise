from __future__ import annotations

import time
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from promise_shared.errors import (
    IntegrationAuthorizationRevoked,
    IntegrationInvalidRequest,
    IntegrationPermissionDenied,
    IntegrationRateLimited,
    IntegrationUnavailable,
)

from .config import GMAIL_AUTH_ENDPOINT, GMAIL_TOKEN_ENDPOINT, GOOGLE_USERINFO_ENDPOINT

PROVIDER_NAME = "gmail"


class GoogleOAuthCredentials(Protocol):
    """The structural shape `GoogleOAuthClient` needs -- satisfied by both
    `gmail.config.GmailConfig` and `calendar.config.CalendarConfig` (each a
    plain dataclass, no shared base class needed; this is exactly what
    `Protocol` is for). Both providers share one Google OAuth *client*
    (never a second, duplicate implementation of the authorization-code
    flow) with two different scope sets/redirect URIs -- see
    `promise_integrations.calendar.oauth` for the Calendar side."""

    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]


def _normalize_token_response(body: dict[str, Any]) -> dict[str, Any]:
    """Google's token endpoint always returns `access_token`/`expires_in`; a
    refresh-token grant does NOT repeat `refresh_token` (see
    `GoogleOAuthClient.refresh_access_token`, which fills it back in from the
    one already on file). `expires_at` here is a plain epoch-seconds float --
    simplest thing a provider can compare against `time.time()` before a call,
    not an ISO string that would need re-parsing every time."""
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires_at": time.time() + float(body.get("expires_in", 3600)),
    }


class GoogleOAuthClient:
    """Server-side Google OAuth 2.0 authorization-code flow, plus two account-identity
    lookups -- shared by every Google-backed provider (Gmail, Google Calendar), never
    duplicated per provider. `get_profile` is Gmail-specific (it calls Gmail's own
    `users.getProfile`, which requires a `gmail.*` scope); `get_identity` is
    provider-neutral (Google's OIDC userinfo endpoint, which only requires the minimal
    `openid`/`userinfo.email` identity scopes) -- Calendar uses `get_identity` so
    identifying the connected account never requires requesting a Gmail scope it
    doesn't otherwise need.

    Talks to Google over plain HTTP via `httpx` (the documented REST endpoints
    -- no `google-auth`/`google-api-python-client` SDK dependency). `transport`
    is injectable so tests exercise this against `httpx.MockTransport` with
    canned responses; no live network or real Google credentials are ever
    required by the normal test suite.
    """

    def __init__(self, config: GoogleOAuthCredentials, *, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def build_authorization_url(self, *, state: str) -> str:
        """`access_type=offline` + `prompt=consent`: Google only issues a
        `refresh_token` on a consent screen, and only when explicitly asked for
        offline access -- required here since PROMISE calls Gmail (to send an
        approved message, or search for context) while the user isn't present
        in a browser. `state` must be the caller's own random, single-use,
        principal-bound token (see `promise_app.gmail_oauth`) -- this method
        never generates or validates it, only carries it through."""
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._config.scopes),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{GMAIL_AUTH_ENDPOINT}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        resp = self._client.post(GMAIL_TOKEN_ENDPOINT, data={
            "code": code,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "redirect_uri": self._config.redirect_uri,
            "grant_type": "authorization_code",
        })
        if resp.status_code >= 500:
            raise IntegrationUnavailable(PROVIDER_NAME, f"authorization code exchange failed (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise IntegrationInvalidRequest(PROVIDER_NAME, "authorization code exchange failed")
        return _normalize_token_response(resp.json())

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        resp = self._client.post(GMAIL_TOKEN_ENDPOINT, data={
            "refresh_token": refresh_token,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "grant_type": "refresh_token",
        })
        if resp.status_code in (400, 401):
            # Google's documented shape for "this refresh token no longer works" is a 400
            # with error=invalid_grant -- either way, this refresh token can never succeed
            # again, so this is authorization revocation, not a transient failure to retry.
            raise IntegrationAuthorizationRevoked(PROVIDER_NAME)
        if resp.status_code >= 500:
            raise IntegrationUnavailable(PROVIDER_NAME, f"token refresh failed (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise IntegrationInvalidRequest(PROVIDER_NAME, "token refresh failed")
        result = _normalize_token_response(resp.json())
        if result["refresh_token"] is None:
            result["refresh_token"] = refresh_token
        return result

    def get_profile(self, *, access_token: str) -> dict[str, Any]:
        """`users.getProfile` -- the only Gmail identity lookup available at
        `gmail.readonly`/`gmail.send` scope (no `openid`/userinfo scope is
        requested, so there's no separate numeric Google account id here; the
        mailbox's own address is the identity)."""
        from .config import GMAIL_API_BASE

        resp = self._client.get(f"{GMAIL_API_BASE}/users/me/profile", headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code == 401:
            raise IntegrationAuthorizationRevoked(PROVIDER_NAME)
        if resp.status_code == 403:
            raise IntegrationPermissionDenied(PROVIDER_NAME)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise IntegrationRateLimited(PROVIDER_NAME, retry_after=float(retry_after) if retry_after else None)
        if resp.status_code >= 500:
            raise IntegrationUnavailable(PROVIDER_NAME, f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise IntegrationInvalidRequest(PROVIDER_NAME, f"HTTP {resp.status_code}")
        body = resp.json()
        return {"email": body["emailAddress"], "history_id": body.get("historyId")}

    def get_identity(self, *, access_token: str) -> dict[str, Any]:
        """Provider-neutral Google account identity via the OIDC userinfo endpoint --
        works for any grant that included the `openid`/`userinfo.email` scopes, no
        matter which product scopes (Gmail, Calendar, ...) rode along with it. This is
        what Calendar OAuth uses to identify the connected account (see
        `promise_app.calendar_oauth`) instead of `get_profile`, since Calendar's own
        scope (`calendar.events.owned`) grants no access to Gmail's `users.getProfile`."""
        resp = self._client.get(GOOGLE_USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code == 401:
            raise IntegrationAuthorizationRevoked(PROVIDER_NAME)
        if resp.status_code == 403:
            raise IntegrationPermissionDenied(PROVIDER_NAME)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise IntegrationRateLimited(PROVIDER_NAME, retry_after=float(retry_after) if retry_after else None)
        if resp.status_code >= 500:
            raise IntegrationUnavailable(PROVIDER_NAME, f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise IntegrationInvalidRequest(PROVIDER_NAME, f"HTTP {resp.status_code}")
        body = resp.json()
        return {"email": body["email"], "sub": body.get("sub")}
