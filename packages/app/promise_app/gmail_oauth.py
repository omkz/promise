from __future__ import annotations

import secrets as _secrets
from datetime import datetime, timedelta, timezone

from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount, OAuthState
from promise_integrations.gmail import GoogleOAuthClient, load_gmail_config
from promise_shared.clock import iso_now
from promise_shared.errors import IntegrationInvalidRequest
from promise_shared.ids import new_id

from .bootstrap import AppContext
from .repos import OAUTH_STATE_PARTITION

"""Application service for the Gmail OAuth 2.0 server-side authorization-code
flow. Kept out of `tools.py` (which is channel-agnostic REST+MCP application
logic) since OAuth is inherently a browser-redirect flow only the REST API's
`/api/integrations/gmail/connect|callback` routes ever drive — MCP has no
notion of it (see the root README's Gmail section for the full picture).

Security model, matching the task spec's explicit requirements:
  - `start_gmail_oauth`'s caller is the authenticated REST principal (the
    router resolves it the normal way, via `get_principal`) — that identity is
    what gets bound into the `OAuthState` row below, never a client-supplied
    workspace_id/user_id.
  - The `state` token is random (`secrets.token_urlsafe`), single-use (the row
    is marked `consumed_at` the moment it's read, and a second read is
    rejected), and expiration-limited (`GOOGLE_OAUTH_STATE_TTL`).
  - `handle_gmail_oauth_callback` derives `workspace_id`/`user_id` *only* from
    the validated `OAuthState` row it looked up by the token Google's redirect
    carries back — never from a query parameter Google (or anyone) could
    otherwise influence.
"""


def start_gmail_oauth(ctx: AppContext, *, workspace_id: str, user_id: str) -> str:
    """Create a fresh OAuth state bound to this principal and return the Google
    authorization URL to redirect the browser to."""
    config = load_gmail_config()
    token = _secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=config.state_ttl_seconds)).isoformat()
    state = OAuthState(
        id=token,
        workspace_id=OAUTH_STATE_PARTITION,
        provider="gmail",
        bound_workspace_id=workspace_id,
        bound_user_id=user_id,
        expires_at=expires_at,
    )
    ctx.repos.oauth_states.save(state)
    client = GoogleOAuthClient(config)
    return client.build_authorization_url(state=token)


def _consume_state(ctx: AppContext, *, state_token: str) -> OAuthState:
    """Validate-and-invalidate in one step: random, single-use, expiration-limited,
    bound to the principal that started the flow -- see module docstring."""
    row = ctx.repos.oauth_states.get(OAUTH_STATE_PARTITION, state_token)
    if row is None:
        raise IntegrationInvalidRequest("gmail", "invalid OAuth state")
    if row.consumed_at is not None:
        raise IntegrationInvalidRequest("gmail", "OAuth state has already been used")
    if row.expires_at < iso_now():
        raise IntegrationInvalidRequest("gmail", "OAuth state has expired")
    return ctx.repos.oauth_states.update(OAUTH_STATE_PARTITION, state_token, lambda s: setattr(s, "consumed_at", iso_now()))


def handle_gmail_oauth_callback(ctx: AppContext, *, state_token: str, code: str) -> IntegrationAccount:
    """Validate `state`, exchange the authorization code, identify the connected
    Gmail account, and create/update its `IntegrationAccount` row. Tokens
    themselves are written only to `ctx.secret_store`, never onto the
    `IntegrationAccount` record itself."""
    state = _consume_state(ctx, state_token=state_token)

    config = load_gmail_config()
    client = GoogleOAuthClient(config)
    tokens = client.exchange_code(code)
    profile = client.get_profile(access_token=tokens["access_token"])

    existing = next(
        iter(ctx.repos.integration_accounts.list_user_owned(state.bound_workspace_id, state.bound_user_id, provider="gmail")),
        None,
    )
    account_id = existing.id if existing else new_id("ia")
    secret_ref = f"gmail:{state.bound_workspace_id}:{account_id}"

    refresh_token = tokens.get("refresh_token")
    if refresh_token is None:
        # Google only returns a refresh_token when the OAuth consent screen was actually
        # shown (which build_authorization_url always requests via prompt=consent, so
        # this should be rare) -- if this is a reconnect and we somehow didn't get one,
        # never silently drop the refresh token already on file.
        prior = ctx.secret_store.get_secret(secret_ref) or {}
        refresh_token = prior.get("refresh_token")
    ctx.secret_store.put_secret(secret_ref, {
        "access_token": tokens["access_token"], "refresh_token": refresh_token, "expires_at": tokens["expires_at"],
    })

    account = IntegrationAccount(
        id=account_id,
        workspace_id=state.bound_workspace_id,
        user_id=state.bound_user_id,
        provider="gmail",
        account_identifier=profile["email"],
        status=IntegrationStatus.CONNECTED,
        scopes=list(config.scopes),
        secret_ref=secret_ref,
        metadata={"history_id": profile.get("history_id")},
        created_at=existing.created_at if existing else iso_now(),
    )
    ctx.repos.integration_accounts.save(account)
    return account
