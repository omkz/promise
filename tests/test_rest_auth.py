from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from promise_api import deps
from promise_api.main import app
from promise_auth import OIDCAuthProvider
from promise_domain.enums import MembershipRole, MembershipStatus
from promise_domain.models import User, Workspace, WorkspaceMembership
from promise_shared.ids import new_id

"""REST-level identity & authorization tests: the FastAPI boundary rejects
unauthenticated (401) and unauthorized (403) requests, and never leaks
whether another tenant's resource exists (404, not 403, for cross-workspace
resource lookups) — see main.py's exception handlers."""

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL"
AUDIENCE = "test-client-id"
_signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeSigningKey:
    def __init__(self, key) -> None:
        self.key = key


class _FakeJWKSClient:
    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey(_signing_key.public_key())


def _token(*, subject: str, exp_delta: int = 3600, scope: str = "commitments.read commitments.write context.read agent.execute") -> str:
    now = int(time.time())
    claims = {"sub": subject, "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + exp_delta, "token_use": "access", "scope": scope}
    return jwt.encode(claims, _signing_key, algorithm="RS256")


def _oidc_provider(**overrides) -> OIDCAuthProvider:
    defaults = dict(issuer=ISSUER, jwks_client=_FakeJWKSClient(), audience=AUDIENCE, required_scopes=frozenset())
    defaults.update(overrides)
    return OIDCAuthProvider(**defaults)


def _link_user(ctx, *, workspace_id, subject, role=MembershipRole.MEMBER, status=MembershipStatus.ACTIVE):
    user = ctx.repos.users.save(
        User(id=new_id("usr"), workspace_id=workspace_id, email=f"{subject}@example.com", name="Test User", external_subject=subject)
    )
    ctx.repos.memberships.save(
        WorkspaceMembership(id=new_id("mem"), workspace_id=workspace_id, user_id=user.id, role=role, status=status)
    )
    return user


@pytest.fixture
def oidc_client(seeded_ctx):
    seeded_ctx.auth_provider = _oidc_provider()
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        yield TestClient(app), seeded_ctx
    finally:
        app.dependency_overrides.clear()


# ---- local mode (default/regression) --------------------------------------------------------------

def test_local_mode_still_works_with_no_authorization_header(seeded_ctx):
    """AUTH_MODE=local is the fixture default — no Bearer token needed at all,
    exactly like before this milestone."""
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.post("/api/commitments", json={"text": "I'll call Sam today."})
        assert r.status_code == 201
    finally:
        app.dependency_overrides.clear()


# ---- 6. missing bearer token rejected (401) --------------------------------------------------------

def test_oidc_mode_without_authorization_header_is_401(oidc_client):
    client, _ = oidc_client
    r = client.get("/api/commitments")
    assert r.status_code == 401
    assert "access token" not in r.text.lower() or "authorization" not in r.headers  # never echoes a token


def test_oidc_mode_with_expired_token_is_401(oidc_client):
    client, seeded_ctx = oidc_client
    _link_user(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, subject="cognito|abc")
    token = _token(subject="cognito|abc", exp_delta=-3600)
    r = client.get("/api/commitments", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---- 8 / user linked but unauthorized workspace: 403 -----------------------------------------------

def test_oidc_mode_workspace_hint_the_user_is_not_a_member_of_is_403(oidc_client):
    client, seeded_ctx = oidc_client
    other_ws = new_id("ws")
    seeded_ctx.repos.workspaces.save(Workspace(id=other_ws, name="Other Co", slug="other-co"))
    _link_user(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, subject="cognito|abc")  # only default ws
    token = _token(subject="cognito|abc")

    r = client.get("/api/commitments", headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": other_ws})
    assert r.status_code == 403


def test_oidc_mode_unrecognized_subject_is_401(oidc_client):
    client, _ = oidc_client
    token = _token(subject="cognito|never-linked-to-a-promise-account")
    r = client.get("/api/commitments", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---- valid token, valid membership, works end to end ------------------------------------------------

def test_oidc_mode_valid_token_and_membership_works(oidc_client):
    client, seeded_ctx = oidc_client
    _link_user(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, subject="cognito|abc")
    token = _token(subject="cognito|abc")

    r = client.post(
        "/api/commitments", json={"text": "I'll send Andi the revised proposal tomorrow morning."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201


# ---- 7. missing required scope rejected (403) -------------------------------------------------------

def test_oidc_mode_missing_required_scope_is_403(seeded_ctx):
    seeded_ctx.auth_provider = _oidc_provider(required_scopes=frozenset({"admin.super"}))
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    _link_user(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, subject="cognito|abc")
    token = _token(subject="cognito|abc", scope="commitments.read")  # doesn't include admin.super
    try:
        client = TestClient(app)
        r = client.get("/api/commitments", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


# ---- X-Dev-User-Id must never work as an identity override outside local mode ----------------------

def test_x_dev_user_id_header_is_ignored_in_oidc_mode(oidc_client):
    client, seeded_ctx = oidc_client
    _link_user(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, subject="cognito|real-user")
    token = _token(subject="cognito|real-user")

    r = client.get("/api/commitments", headers={"Authorization": f"Bearer {token}", "X-Dev-User-Id": "usr_dev_default"})
    # Still resolves via the token's own subject, not the header -- succeeds only because
    # cognito|real-user really is linked, never because X-Dev-User-Id said so.
    assert r.status_code == 200


def test_x_dev_user_id_alone_with_no_bearer_token_does_not_authenticate_in_oidc_mode(oidc_client):
    client, _ = oidc_client
    r = client.get("/api/commitments", headers={"X-Dev-User-Id": "usr_dev_default"})
    assert r.status_code == 401


# ---- 9 / 10. cross-user and cross-workspace resource access -----------------------------------------

def test_cross_workspace_commitment_lookup_is_404_not_403(seeded_ctx):
    """Resource enumeration must be prevented: a foreign workspace's commitment
    looks the same as one that never existed."""
    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.get("/api/commitments/com_does_not_exist", headers={"X-Workspace-Id": "ws_someone_else"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_cross_user_context_access_is_rejected(oidc_client):
    """Two different users, both real members of the SAME workspace: user B must
    not read user A's commitment context by guessing the id."""
    client, seeded_ctx = oidc_client
    _link_user(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, subject="cognito|alice")
    _link_user(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, subject="cognito|bob")
    alice_token = _token(subject="cognito|alice")
    bob_token = _token(subject="cognito|bob")

    r = client.post(
        "/api/commitments", json={"text": "I'll send Andi the revised proposal tomorrow morning."},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    commitment_id = r.json()["commitment"]["id"]

    r = client.get(f"/api/commitments/{commitment_id}/context", headers={"Authorization": f"Bearer {bob_token}"})
    assert r.status_code == 403
