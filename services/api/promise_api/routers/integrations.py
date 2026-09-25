from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from promise_app import gmail_oauth, tools
from promise_app.bootstrap import AppContext
from promise_auth import AuthenticatedPrincipal, Permission, require
from promise_domain.models import IntegrationAccount
from promise_shared.errors import PromiseError
from pydantic import BaseModel

from ..deps import get_context, get_principal

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
logger = logging.getLogger("promise.integrations")


class ConnectIntegrationBody(BaseModel):
    provider: str
    account_identifier: str
    scopes: list[str] = []


@router.get("")
def list_integrations(
    principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> list[IntegrationAccount]:
    require(principal, Permission.INTEGRATIONS_MANAGE)
    return tools.list_integration_accounts(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id)


@router.post("", status_code=201)
def connect_integration(
    body: ConnectIntegrationBody, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> IntegrationAccount:
    require(principal, Permission.INTEGRATIONS_MANAGE)
    return tools.connect_integration_account(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, provider=body.provider,
        account_identifier=body.account_identifier, scopes=body.scopes,
    )


@router.post("/{account_id}/disconnect")
def disconnect_integration(
    account_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> IntegrationAccount:
    require(principal, Permission.INTEGRATIONS_MANAGE)
    return tools.disconnect_integration_account(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, account_id=account_id
    )


# ---- Gmail OAuth ----------------------------------------------------------------------------
#
# `gmail_oauth.py` (promise_app) holds all the actual OAuth/token logic; these routes only
# translate transport <-> that application service, same as every other route in this file.


@router.get("/gmail/connect")
def gmail_connect(
    principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> dict[str, str]:
    """Returns the Google authorization URL for the *client* to navigate to --
    deliberately not an HTTP redirect from this endpoint itself. This call
    must carry the authenticated principal's own headers (`X-Dev-User-Id` /
    `Authorization: Bearer`, see `web/lib/auth.ts`), and a browser's top-level
    navigation to a plain link can never attach those; a `fetch()` call can.
    The web client fetches this JSON, then performs the actual
    `window.location` navigation itself."""
    require(principal, Permission.INTEGRATIONS_MANAGE)
    url = gmail_oauth.start_gmail_oauth(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id)
    return {"authorization_url": url}


@router.get("/gmail/callback")
def gmail_callback(
    state: str, code: str | None = None, error: str | None = None, ctx: AppContext = Depends(get_context)
) -> RedirectResponse:
    """Google's own redirect back to PROMISE after the user grants (or denies)
    consent -- a top-level browser navigation from `accounts.google.com` that
    carries no PROMISE auth headers at all, so this route is intentionally
    not behind `get_principal`. Identity instead comes entirely from the
    validated, principal-bound `state` row this callback's own `state` query
    parameter points at (see `gmail_oauth._consume_state`) -- workspace_id/
    user_id are never taken from a query parameter directly. Never returns
    access/refresh tokens to the browser; always ends in a redirect to the
    web app with only a coarse status indicator in the URL.
    """
    web_app_url = os.getenv("WEB_APP_URL", "http://localhost:3000").rstrip("/")
    if error:
        logger.info("gmail_oauth_callback denied_by_user error=%s", error)
        return RedirectResponse(f"{web_app_url}/connections?gmail=error&reason=denied")
    if not code:
        return RedirectResponse(f"{web_app_url}/connections?gmail=error&reason=missing_code")
    try:
        account = gmail_oauth.handle_gmail_oauth_callback(ctx, state_token=state, code=code)
    except PromiseError as exc:
        # Never leaks the authorization code, a token, or the raw provider response --
        # `str(exc)` here only ever carries the short, fixed detail promise_shared.errors
        # builds (see IntegrationInvalidRequest / IntegrationUnavailable / ...).
        logger.warning("gmail_oauth_callback failed error_type=%s detail=%s", type(exc).__name__, exc)
        return RedirectResponse(f"{web_app_url}/connections?gmail=error")
    logger.info("gmail_oauth_callback connected workspace=%s account=%s", account.workspace_id, account.id)
    return RedirectResponse(f"{web_app_url}/connections?gmail=connected")
