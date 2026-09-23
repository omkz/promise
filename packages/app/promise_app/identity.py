from __future__ import annotations

import logging

from promise_auth import AuthenticatedPrincipal, AuthRequest, TokenClaims, permissions_for_role
from promise_domain.enums import MembershipStatus
from promise_domain.models import User, WorkspaceMembership
from promise_shared.errors import AuthenticationRequired, PromiseError, WorkspaceAccessDenied
from promise_shared.ids import new_id
from promise_shared.store import EntityStore

from .bootstrap import AppContext

"""Identity resolution: `TokenClaims` (verified, but workspace-less) ->
`AuthenticatedPrincipal` (verified, and scoped to one membership-checked
workspace). This is the ONLY place in the codebase allowed to decide which
workspace a request acts in — see `AuthenticatedPrincipal`'s docstring.

Deliberately lives in `promise_app`, not `promise_auth`: resolving "subject ->
User -> WorkspaceMembership" needs the domain repositories, and `promise_auth`
must stay independent of `promise_domain`/storage so it can be unit-tested
(and reasoned about) with zero I/O.
"""

logger = logging.getLogger("promise.auth")


def authenticate(ctx: AppContext, request: AuthRequest) -> AuthenticatedPrincipal:
    """The single entry point every transport adapter (REST `deps.py`, MCP
    `server.py`) calls: raw `AuthRequest` -> verified `AuthProvider.authenticate`
    -> membership-checked `resolve_principal`. Neither step is skippable from
    here — there is no code path that hands back a principal without both.

    Logs `request_id`/`user_id`/`workspace_id`/`auth_method` on success and
    `request_id`/`auth_method`/the error class on failure — never the
    Authorization header, the token itself, or any other secret value.
    """
    request_id = new_id("req")
    try:
        claims = ctx.auth_provider.authenticate(request)
        requested_workspace_id = request.requested_workspace_id or claims.dev_workspace_hint
        principal = resolve_principal(ctx.store, claims, requested_workspace_id=requested_workspace_id)
    except PromiseError as exc:
        logger.info(
            "auth_failed request_id=%s auth_method=%s error_type=%s",
            request_id, ctx.auth_provider.auth_method, type(exc).__name__,
        )
        raise
    logger.info(
        "auth_succeeded request_id=%s user_id=%s workspace_id=%s auth_method=%s",
        request_id, principal.user_id, principal.workspace_id, principal.auth_method,
    )
    return principal


def resolve_principal(store: EntityStore, claims: TokenClaims, *, requested_workspace_id: str | None = None) -> AuthenticatedPrincipal:
    if claims.auth_method == "local":
        return _resolve_local(claims, requested_workspace_id)
    return _resolve_oidc(store, claims, requested_workspace_id)


def _resolve_local(claims: TokenClaims, requested_workspace_id: str | None) -> AuthenticatedPrincipal:
    """AUTH_MODE=local: trusts the already-resolved dev identity directly — no
    membership lookup, no domain query. Gated behind `LocalAuthProvider`
    itself only ever being constructed when `AUTH_MODE=local`; not production
    security, and role is always "owner" purely for developer convenience."""
    user_id = claims.subject.removeprefix("local:")
    workspace_id = requested_workspace_id or claims.dev_workspace_hint
    if not workspace_id:
        raise AuthenticationRequired("local auth mode could not determine a workspace")
    role = "owner"
    return AuthenticatedPrincipal(
        subject=claims.subject, user_id=user_id, workspace_id=workspace_id, role=role,
        permissions=permissions_for_role(role), scopes=claims.scopes, auth_method="local",
    )


def _resolve_oidc(store: EntityStore, claims: TokenClaims, requested_workspace_id: str | None) -> AuthenticatedPrincipal:
    user = _find_user_by_subject(store, claims.subject)
    if user is None:
        # Deliberately AuthenticationRequired, not WorkspaceAccessDenied: a verified token
        # for an identity PROMISE has never linked to a User is an auth-boundary problem,
        # not a workspace-membership one.
        raise AuthenticationRequired("no PROMISE account is linked to this identity")

    active_memberships = [m for m in _memberships_for_user(store, user.id) if m.status == MembershipStatus.ACTIVE]
    if not active_memberships:
        raise WorkspaceAccessDenied(requested_workspace_id or "(none)")

    if requested_workspace_id:
        membership = next((m for m in active_memberships if m.workspace_id == requested_workspace_id), None)
        if membership is None:
            # Never reveals whether requested_workspace_id even exists — same shape of
            # error whether the workspace is real-but-foreign or entirely made up.
            raise WorkspaceAccessDenied(requested_workspace_id)
    else:
        membership = active_memberships[0]

    role = membership.role.value
    return AuthenticatedPrincipal(
        subject=claims.subject, user_id=user.id, workspace_id=membership.workspace_id, role=role,
        permissions=permissions_for_role(role), scopes=claims.scopes, auth_method="oidc",
    )


def _find_user_by_subject(store: EntityStore, subject: str) -> User | None:
    """The one legitimate cross-workspace `query_all` outside admin/maintenance
    tooling: identity resolution can't know which workspace a subject belongs
    to before it has resolved the user. A real deployment would back this with
    a proper index (e.g. a DynamoDB GSI on `external_subject`) rather than a
    linear scan — see the README's known-limitations note."""
    for row in store.query_all("user"):
        if row.get("external_subject") == subject:
            return User.model_validate(row)
    return None


def _memberships_for_user(store: EntityStore, user_id: str) -> list[WorkspaceMembership]:
    return [
        WorkspaceMembership.model_validate(row) for row in store.query_all("workspace_membership") if row.get("user_id") == user_id
    ]
