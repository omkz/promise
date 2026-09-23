from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header
from promise_app.bootstrap import AppContext, build_context
from promise_app.identity import authenticate
from promise_auth import AuthenticatedPrincipal, AuthRequest

"""FastAPI's identity boundary: every route depends on `get_principal`, never
on a raw header, so `promise_app`/`promise_domain` never see an untrusted
`user_id`. See `promise_app.identity.authenticate` for what actually verifies
and resolves it, and `promise_auth` for the `AuthProvider` abstraction
(`AUTH_MODE=local` vs. `AUTH_MODE=oidc`) underneath that.
"""


@lru_cache
def _default_context() -> AppContext:
    return build_context()


def get_context() -> AppContext:
    """One AppContext per process by default. Routed through FastAPI's DI (not
    called directly) so tests can swap it via `app.dependency_overrides`."""
    return _default_context()


def get_principal(
    authorization: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    ctx: AppContext = Depends(get_context),
) -> AuthenticatedPrincipal:
    """Resolves and membership-checks the caller in one step.

    `Authorization: Bearer <token>` is the only credential `AUTH_MODE=oidc`
    ever reads; `X-Dev-User-Id` is honored *only* by `AUTH_MODE=local`'s
    `LocalAuthProvider` — an `OIDCAuthProvider` never looks at it, so it can
    never be used to impersonate another user in production. `X-Workspace-Id`
    is a "which of my workspaces" hint valid in both modes, but in oidc mode
    it's cross-checked against the resolved user's real `WorkspaceMembership`
    rows (`WorkspaceAccessDenied` if there isn't an active one) — it is never
    trusted on its own.

    Raises `AuthenticationRequired`/`InvalidToken`/`TokenExpired` (-> 401) or
    `InsufficientScope`/`WorkspaceAccessDenied` (-> 403); see `main.py`'s
    exception handlers.
    """
    request = AuthRequest(authorization_header=authorization, requested_workspace_id=x_workspace_id, dev_user_id=x_dev_user_id)
    return authenticate(ctx, request)
