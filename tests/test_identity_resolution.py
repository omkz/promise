from __future__ import annotations

import pytest
from promise_app.bootstrap import build_auth_provider
from promise_app.identity import authenticate, resolve_principal
from promise_auth import AuthRequest, LocalAuthProvider, TokenClaims
from promise_domain.enums import MembershipRole, MembershipStatus
from promise_domain.models import User, Workspace, WorkspaceMembership
from promise_shared.errors import AuthenticationRequired, WorkspaceAccessDenied
from promise_shared.ids import new_id

"""Tests for `promise_app.identity` — the "subject -> User -> WorkspaceMembership
-> AuthenticatedPrincipal" resolution step. `promise_auth` itself (JWT/JWKS
validation) is covered by `tests/test_auth_oidc.py`; these tests exercise the
domain-aware resolution `promise_auth` deliberately doesn't do itself."""


def _link_user(ctx, *, workspace_id, subject, role=MembershipRole.MEMBER, status=MembershipStatus.ACTIVE):
    user = ctx.repos.users.save(
        User(id=new_id("usr"), workspace_id=workspace_id, email=f"{subject}@example.com", name="Test User", external_subject=subject)
    )
    ctx.repos.memberships.save(
        WorkspaceMembership(id=new_id("mem"), workspace_id=workspace_id, user_id=user.id, role=role, status=status)
    )
    return user


def _claims(subject: str) -> TokenClaims:
    return TokenClaims(subject=subject, scopes=frozenset(), auth_method="oidc")


# ---- subject -> user -> membership -> principal, the happy path --------------------------------

def test_oidc_principal_resolves_via_subject_user_membership(ctx):
    user = _link_user(ctx, workspace_id=ctx.default_workspace_id, subject="cognito|abc123")

    principal = resolve_principal(ctx.store, _claims("cognito|abc123"), requested_workspace_id=ctx.default_workspace_id)

    assert principal.user_id == user.id
    assert principal.workspace_id == ctx.default_workspace_id
    assert principal.auth_method == "oidc"
    assert principal.role == "member"


def test_owner_role_grants_more_permissions_than_member(ctx):
    from promise_auth import Permission

    _link_user(ctx, workspace_id=ctx.default_workspace_id, subject="cognito|owner-1", role=MembershipRole.OWNER)
    _link_user(ctx, workspace_id=ctx.default_workspace_id, subject="cognito|member-1", role=MembershipRole.MEMBER)

    owner = resolve_principal(ctx.store, _claims("cognito|owner-1"), requested_workspace_id=ctx.default_workspace_id)
    member = resolve_principal(ctx.store, _claims("cognito|member-1"), requested_workspace_id=ctx.default_workspace_id)

    assert owner.has_permission(Permission.INTEGRATIONS_MANAGE)
    assert not member.has_permission(Permission.INTEGRATIONS_MANAGE)
    assert member.has_permission(Permission.COMMITMENTS_READ)


def test_unrecognized_subject_is_rejected_not_mapped_to_any_default(ctx):
    with pytest.raises(AuthenticationRequired):
        resolve_principal(ctx.store, _claims("cognito|nobody-knows-this-one"), requested_workspace_id=ctx.default_workspace_id)


# ---- 8. valid user but unauthorized workspace rejected ------------------------------------------

def test_valid_user_but_unauthorized_workspace_is_rejected(ctx):
    other_ws_id = new_id("ws")
    ctx.repos.workspaces.save(Workspace(id=other_ws_id, name="Other Co", slug="other-co"))
    _link_user(ctx, workspace_id=ctx.default_workspace_id, subject="cognito|abc123")  # only a member of the default ws

    with pytest.raises(WorkspaceAccessDenied):
        resolve_principal(ctx.store, _claims("cognito|abc123"), requested_workspace_id=other_ws_id)


def test_inactive_membership_is_rejected(ctx):
    _link_user(ctx, workspace_id=ctx.default_workspace_id, subject="cognito|inactive-user", status=MembershipStatus.INACTIVE)
    with pytest.raises(WorkspaceAccessDenied):
        resolve_principal(ctx.store, _claims("cognito|inactive-user"), requested_workspace_id=ctx.default_workspace_id)


def test_no_requested_workspace_falls_back_to_the_users_own_active_membership(ctx):
    user = _link_user(ctx, workspace_id=ctx.default_workspace_id, subject="cognito|abc123")
    principal = resolve_principal(ctx.store, _claims("cognito|abc123"), requested_workspace_id=None)
    assert principal.user_id == user.id
    assert principal.workspace_id == ctx.default_workspace_id


# ---- 10. cross-workspace access rejected (a second, independent workspace/user pair) -----------

def test_membership_in_one_workspace_does_not_grant_another(ctx):
    ws_a = new_id("ws")
    ws_b = new_id("ws")
    ctx.repos.workspaces.save(Workspace(id=ws_a, name="A Co", slug="a-co"))
    ctx.repos.workspaces.save(Workspace(id=ws_b, name="B Co", slug="b-co"))
    _link_user(ctx, workspace_id=ws_a, subject="cognito|alice")

    principal = resolve_principal(ctx.store, _claims("cognito|alice"), requested_workspace_id=ws_a)
    assert principal.workspace_id == ws_a

    with pytest.raises(WorkspaceAccessDenied):
        resolve_principal(ctx.store, _claims("cognito|alice"), requested_workspace_id=ws_b)


# ---- 13. local auth mode works -------------------------------------------------------------------

def test_local_mode_resolves_without_any_membership_lookup(ctx):
    claims = TokenClaims(subject="local:usr_dev_default", scopes=frozenset({"*"}), auth_method="local", dev_workspace_hint=ctx.default_workspace_id)
    principal = resolve_principal(ctx.store, claims, requested_workspace_id=None)
    assert principal.user_id == "usr_dev_default"
    assert principal.workspace_id == ctx.default_workspace_id
    assert principal.role == "owner"


def test_authenticate_end_to_end_in_local_mode(ctx):
    ctx.auth_provider = LocalAuthProvider(default_user_id=ctx.default_user_id, default_workspace_id=ctx.default_workspace_id)
    principal = authenticate(ctx, AuthRequest())
    assert principal.user_id == ctx.default_user_id
    assert principal.workspace_id == ctx.default_workspace_id
    assert principal.auth_method == "local"


# ---- 14. OIDC mode never falls back to local identity --------------------------------------------

def test_oidc_resolution_never_falls_back_to_the_local_dev_identity(ctx):
    """An unrecognized subject in oidc mode must fail, never silently resolve to
    ws_dev_default/usr_dev_default."""
    with pytest.raises(AuthenticationRequired) as excinfo:
        resolve_principal(ctx.store, _claims("cognito|totally-unknown"), requested_workspace_id=None)
    assert "usr_dev_default" not in str(excinfo.value)


def test_build_auth_provider_oidc_mode_requires_an_issuer(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.delenv("COGNITO_ISSUER", raising=False)
    with pytest.raises(RuntimeError, match="COGNITO_ISSUER"):
        build_auth_provider()


def test_build_auth_provider_rejects_an_unknown_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "yolo")
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        build_auth_provider()


def test_build_auth_provider_local_mode_returns_a_local_provider(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    provider = build_auth_provider()
    assert isinstance(provider, LocalAuthProvider)
    assert provider.auth_method == "local"


# ---- 15. no token leakage in logs -----------------------------------------------------------------

def test_authenticate_logs_never_include_the_bearer_token_or_header(ctx, caplog):
    import logging

    bogus_token = "this-looks-like-a-jwt-but-isnt.super.secret"
    with caplog.at_level(logging.INFO, logger="promise.auth"):
        try:
            authenticate(ctx, AuthRequest(authorization_header=f"Bearer {bogus_token}"))
        except Exception:
            pass  # local mode ignores the header anyway; oidc-mode leakage is what this test guards
    for record in caplog.records:
        assert bogus_token not in record.getMessage()
        assert "Bearer" not in record.getMessage()


def test_authenticate_logs_user_and_workspace_on_success(ctx, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="promise.auth"):
        authenticate(ctx, AuthRequest())
    messages = [r.getMessage() for r in caplog.records]
    assert any("auth_succeeded" in m and ctx.default_user_id in m and ctx.default_workspace_id in m for m in messages)
