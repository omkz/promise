from __future__ import annotations

import inspect
import time

import jwt
import promise_mcp.server as mcp_server
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from promise_auth import OIDCAuthProvider
from promise_domain.enums import MembershipRole, MembershipStatus
from promise_domain.models import User, WorkspaceMembership
from promise_shared.errors import AuthenticationRequired
from promise_shared.ids import new_id

"""MCP-adapter identity tests: tools derive identity from the authenticated
request context (`Context.headers`), never from a tool argument. See
`tests/test_mcp_adapter.py`/`test_mcp_apps_ui.py` for the pre-existing
functional coverage this milestone must keep passing unmodified (identity
source changes; application behavior doesn't)."""

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL"
_signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeSigningKey:
    def __init__(self, key) -> None:
        self.key = key


class _FakeJWKSClient:
    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey(_signing_key.public_key())


class _FakeMcpContext:
    """A minimal duck-typed stand-in for `mcp.server.mcpserver.context.Context`:
    `_authenticate` only ever reads `.headers`, so this is all a unit test needs
    — constructing a *real* `Context` requires a live, transport-bound
    `ServerRequestContext`, which is exactly what these tests don't have (and
    don't need; the real end-to-end wiring is exercised by
    `tests/test_mcp_apps_ui.py` going through `mcp.call_tool`)."""

    def __init__(self, headers: dict[str, str] | None) -> None:
        self.headers = headers


def _token(*, subject: str, exp_delta: int = 3600) -> str:
    now = int(time.time())
    claims = {
        "sub": subject, "iss": ISSUER, "aud": "test-client-id", "iat": now, "exp": now + exp_delta,
        "token_use": "access", "scope": "commitments.read commitments.write context.read agent.execute",
    }
    return jwt.encode(claims, _signing_key, algorithm="RS256")


def _oidc_provider() -> OIDCAuthProvider:
    return OIDCAuthProvider(issuer=ISSUER, jwks_client=_FakeJWKSClient(), audience="test-client-id", required_scopes=frozenset())


def _link_user(ctx, *, workspace_id, subject):
    user = ctx.repos.users.save(
        User(id=new_id("usr"), workspace_id=workspace_id, email=f"{subject}@example.com", name="Test User", external_subject=subject)
    )
    ctx.repos.memberships.save(
        WorkspaceMembership(id=new_id("mem"), workspace_id=workspace_id, user_id=user.id, role=MembershipRole.MEMBER, status=MembershipStatus.ACTIVE)
    )
    return user


_TOOL_NAMES = [
    "create_commitment", "update_commitment", "search_commitments", "retrieve_commitment_context",
    "search_files", "get_file", "search_messages", "get_message", "prepare_revision", "create_draft",
    "handle_commitment", "request_approval", "decide_approval", "execute_approved_action", "complete_commitment",
]


# ---- 12. MCP cannot override user_id/workspace_id --------------------------------------------------

@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_no_mcp_tool_accepts_workspace_id_or_user_id_as_an_argument(tool_name):
    fn = getattr(mcp_server, tool_name)
    params = inspect.signature(fn).parameters
    forbidden = {"workspace_id", "user_id", "actor", "decided_by"}
    assert forbidden.isdisjoint(params), f"{tool_name} still accepts {forbidden & set(params)} as a tool argument"


def test_update_commitment_rejects_a_workspace_id_smuggled_through_kwargs(seeded_ctx, monkeypatch):
    """update_commitment takes **fields for the mutable commitment fields — proves
    a client can't smuggle workspace_id through that catch-all either: it collides
    with the real, principal-derived workspace_id the tool already passes to
    `tools.update_commitment`, so Python itself rejects the call outright."""
    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    created = mcp_server.create_commitment(text="I'll send Andi the revised proposal tomorrow morning.")
    commitment_id = created.structured_content["commitment"]["id"]

    with pytest.raises(TypeError):
        mcp_server.update_commitment(commitment_id, workspace_id="ws_someone_else")


# ---- 11. MCP uses the authenticated principal ------------------------------------------------------

def test_mcp_tool_resolves_identity_from_context_headers(seeded_ctx, monkeypatch):
    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    seeded_ctx.auth_provider = _oidc_provider()
    _link_user(seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, subject="cognito|alexa-user")
    token = _token(subject="cognito|alexa-user")

    fake_ctx = _FakeMcpContext(headers={"authorization": f"Bearer {token}"})
    result = mcp_server.search_commitments(query="", mcp_ctx=fake_ctx)
    assert result == []  # authenticated fine, just no commitments yet


def test_mcp_tool_rejects_a_request_with_no_authorization_header_in_oidc_mode(seeded_ctx, monkeypatch):
    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    seeded_ctx.auth_provider = _oidc_provider()

    fake_ctx = _FakeMcpContext(headers={})
    with pytest.raises(AuthenticationRequired):
        mcp_server.search_commitments(query="", mcp_ctx=fake_ctx)


def test_mcp_tool_with_missing_context_falls_back_to_configured_provider_not_a_bypass(seeded_ctx, monkeypatch):
    """`mcp_ctx=None` (no live request bound) still goes through the real
    AuthProvider with an empty AuthRequest — in oidc mode that still fails
    closed, it's never a shortcut around authentication."""
    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    seeded_ctx.auth_provider = _oidc_provider()

    with pytest.raises(AuthenticationRequired):
        mcp_server.search_commitments(query="", mcp_ctx=None)


# ---- 14. OIDC mode never falls back to local identity (MCP side) -----------------------------------

def test_mcp_oidc_mode_never_uses_local_dev_identity(seeded_ctx, monkeypatch):
    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    seeded_ctx.auth_provider = _oidc_provider()

    with pytest.raises(AuthenticationRequired) as excinfo:
        mcp_server.search_commitments(query="", mcp_ctx=_FakeMcpContext(headers=None))
    assert seeded_ctx.default_user_id not in str(excinfo.value)
