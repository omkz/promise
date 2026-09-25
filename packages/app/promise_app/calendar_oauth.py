from __future__ import annotations

import secrets as _secrets
from datetime import datetime, timedelta, timezone

from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount, OAuthState
from promise_integrations.calendar import load_calendar_config
from promise_integrations.gmail import GoogleOAuthClient
from promise_shared.clock import iso_now
from promise_shared.errors import IntegrationInvalidRequest
from promise_shared.ids import new_id

from .bootstrap import AppContext
from .repos import OAUTH_STATE_PARTITION

"""Application service for the Google Calendar OAuth 2.0 server-side
authorization-code flow -- the exact same shape as `gmail_oauth.py`, reusing
the same shared `GoogleOAuthClient` (see that module's docstring) with a
`CalendarConfig` in place of a `GmailConfig`. Deliberately NOT a second
independent OAuth implementation: only the config/scopes/redirect URI, the
`IntegrationAccount.provider`/secret_ref namespace, and the identity lookup
(`get_identity` here vs `get_profile` in `gmail_oauth.py` -- Calendar's own
scope grants no access to Gmail's `users.getProfile`, so account identity
comes from Google's provider-neutral OIDC userinfo endpoint instead) differ
from Gmail's.

Incremental authorization: because Calendar uses its own registered redirect
URI (`GOOGLE_CALENDAR_REDIRECT_URI`) and its own narrow scope set
(`calendar.events.owned` by default), a user who already connected Gmail can
connect Calendar separately without ever being asked to reconnect Gmail --
each provider gets its own `IntegrationAccount` row (`provider="gmail"` vs
`provider="google_calendar"`) and its own `secret_ref`. Google's consent
screen will only prompt for the additional Calendar scope, not re-request
Gmail's, since `prompt=consent` plus a distinct scope set is exactly what
"incremental authorization" means in Google's OAuth model -- no separate
incremental-auth code path is needed here.

Security model matches `gmail_oauth.py`'s exactly -- see that module's
docstring for the full rationale (random/single-use/expiring `state`,
identity derived only from the validated `OAuthState` row, never from a
query parameter).
"""


def start_calendar_oauth(ctx: AppContext, *, workspace_id: str, user_id: str) -> str:
    """Create a fresh OAuth state bound to this principal and return the Google
    authorization URL to redirect the browser to."""
    config = load_calendar_config()
    token = _secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=config.state_ttl_seconds)).isoformat()
    state = OAuthState(
        id=token,
        workspace_id=OAUTH_STATE_PARTITION,
        provider="google_calendar",
        bound_workspace_id=workspace_id,
        bound_user_id=user_id,
        expires_at=expires_at,
    )
    ctx.repos.oauth_states.save(state)
    client = GoogleOAuthClient(config)
    return client.build_authorization_url(state=token)


def _consume_state(ctx: AppContext, *, state_token: str) -> OAuthState:
    """Validate-and-invalidate in one step -- see module docstring."""
    row = ctx.repos.oauth_states.get(OAUTH_STATE_PARTITION, state_token)
    if row is None:
        raise IntegrationInvalidRequest("google_calendar", "invalid OAuth state")
    if row.consumed_at is not None:
        raise IntegrationInvalidRequest("google_calendar", "OAuth state has already been used")
    if row.expires_at < iso_now():
        raise IntegrationInvalidRequest("google_calendar", "OAuth state has expired")
    return ctx.repos.oauth_states.update(OAUTH_STATE_PARTITION, state_token, lambda s: setattr(s, "consumed_at", iso_now()))


def handle_calendar_oauth_callback(ctx: AppContext, *, state_token: str, code: str) -> IntegrationAccount:
    """Validate `state`, exchange the authorization code, identify the connected
    Google account, and create/update its Calendar `IntegrationAccount` row.
    Tokens themselves are written only to `ctx.secret_store`, never onto the
    `IntegrationAccount` record itself."""
    state = _consume_state(ctx, state_token=state_token)

    config = load_calendar_config()
    client = GoogleOAuthClient(config)
    tokens = client.exchange_code(code)
    # `get_identity`, not `get_profile`: Calendar's own scope (`calendar.events.owned`,
    # plus the minimal `openid`/`userinfo.email` identity scopes -- see
    # `CalendarConfig`'s docstring) grants no access to Gmail's `users.getProfile`, so
    # identity here comes from Google's provider-neutral OIDC userinfo endpoint instead.
    identity = client.get_identity(access_token=tokens["access_token"])

    existing = next(
        iter(
            ctx.repos.integration_accounts.list_user_owned(
                state.bound_workspace_id, state.bound_user_id, provider="google_calendar"
            )
        ),
        None,
    )
    account_id = existing.id if existing else new_id("ia")
    secret_ref = f"google_calendar:{state.bound_workspace_id}:{account_id}"

    refresh_token = tokens.get("refresh_token")
    if refresh_token is None:
        # Same "never silently drop a refresh token already on file" rule as
        # gmail_oauth.py -- see that module's identical comment for why.
        prior = ctx.secret_store.get_secret(secret_ref) or {}
        refresh_token = prior.get("refresh_token")
    ctx.secret_store.put_secret(secret_ref, {
        "access_token": tokens["access_token"], "refresh_token": refresh_token, "expires_at": tokens["expires_at"],
    })

    account = IntegrationAccount(
        id=account_id,
        workspace_id=state.bound_workspace_id,
        user_id=state.bound_user_id,
        provider="google_calendar",
        account_identifier=identity["email"],
        status=IntegrationStatus.CONNECTED,
        scopes=list(config.scopes),
        secret_ref=secret_ref,
        metadata={},
        created_at=existing.created_at if existing else iso_now(),
    )
    ctx.repos.integration_accounts.save(account)
    return account
