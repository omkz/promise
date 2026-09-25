from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx
from promise_shared.errors import IntegrationAuthorizationRevoked, IntegrationNotConnected, IntegrationTokenExpired
from promise_shared.secrets import SecretStore

if TYPE_CHECKING:
    from .gmail.oauth import GoogleOAuthCredentials

# Refresh a bit before actual expiry so a call never races Google's own clock.
_TOKEN_REFRESH_SKEW_SECONDS = 60

# `GoogleOAuthClient` is imported lazily (inside __init__, not at module load time):
# `gmail/__init__.py` eagerly imports `gmail/provider.py`, which imports THIS module --
# a top-level `from .gmail.oauth import GoogleOAuthClient` here would re-enter
# `gmail/__init__.py` before it finishes, a circular import. Deferring the import to
# first use avoids it entirely without weakening anything -- by the time any provider
# actually constructs a GoogleTokenManager, module loading has long finished.


class GoogleTokenManager:
    """Shared OAuth access-token lifecycle for every Google-backed provider
    (Gmail, Google Calendar) -- one place for "get a valid access token right
    now," reused rather than reimplemented per provider. Composed by
    `GmailIntegrationProvider`/`GoogleCalendarIntegrationProvider` (has-a, not
    inherited): each provider keeps its own request/response shapes and its
    own 401-retry orchestration (via `access_token(force_refresh=True)`),
    only the refresh/expiry/revocation logic itself is shared here.

    Refreshes automatically on expiry; on an explicit `force_refresh=True`
    (a provider's own response to an unexpected 401); and fails closed the
    moment Google reports the refresh token revoked -- the stored secret is
    deleted immediately (never retried again), so every subsequent call for
    this account fails closed with `IntegrationNotConnected`/
    `IntegrationTokenExpired`, not a repeated revoked-token error against
    Google.
    """

    def __init__(
        self, *, provider_name: str, secret_ref: str, secret_store: SecretStore,
        config: GoogleOAuthCredentials, transport: httpx.BaseTransport | None = None,
    ) -> None:
        from .gmail.oauth import GoogleOAuthClient

        self._provider_name = provider_name
        self._secret_ref = secret_ref
        self._secret_store = secret_store
        self._oauth = GoogleOAuthClient(config, transport=transport)

    def _refresh(self, refresh_token: str) -> dict[str, Any]:
        try:
            refreshed = self._oauth.refresh_access_token(refresh_token)
        except IntegrationAuthorizationRevoked:
            self._secret_store.delete_secret(self._secret_ref)
            raise
        self._secret_store.put_secret(self._secret_ref, refreshed)
        return refreshed

    def access_token(self, *, force_refresh: bool = False) -> str:
        secret = self._secret_store.get_secret(self._secret_ref)
        if secret is None:
            raise IntegrationNotConnected(self._provider_name)
        if not force_refresh and secret.get("expires_at", 0) > time.time() + _TOKEN_REFRESH_SKEW_SECONDS:
            return secret["access_token"]
        refresh_token = secret.get("refresh_token")
        if not refresh_token:
            raise IntegrationTokenExpired(self._provider_name)
        return self._refresh(refresh_token)["access_token"]
