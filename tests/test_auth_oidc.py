from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from promise_auth import AuthRequest, OIDCAuthProvider
from promise_auth.provider import LocalAuthProvider
from promise_shared.errors import AuthenticationRequired, InsufficientScope, InvalidToken, TokenExpired

"""Unit tests for `OIDCAuthProvider` — standard JWT validation principles,
against generated RSA keypair / self-signed-JWT fixtures. No live Cognito or
network access required (see `_FakeJWKSClient`).

Token shapes here deliberately match real Amazon Cognito claims: an *access*
token carries `client_id` and `scope`, and has NO `aud` claim at all; an *ID*
token carries `aud` and has no OAuth `scope`. See `oidc.py`'s module
docstring for why conflating the two (validating a Cognito access token's
`client_id` as if it were `aud`) is the bug this file guards against.
"""

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL"
CLIENT_ID = "test-client-id"

_signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # a different, "attacker" key


class _FakeSigningKey:
    def __init__(self, key) -> None:
        self.key = key


class _FakeJWKSClient:
    """Deterministic stand-in for `jwt.PyJWKClient` — always resolves to the
    same known public key, no network access, so JWKS fetching/caching is
    never exercised by these tests (that's PyJWT's own well-tested code)."""

    def __init__(self, public_key) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey(self._public_key)


def _token(
    *, subject: str = "user-abc", issuer: str = ISSUER, token_use: str | None = "access", client_id: str | None = CLIENT_ID,
    audience: str | None = None, scope: str | None = "commitments.read", exp_delta: int = 3600,
    signing_key=_signing_key, extra_claims: dict | None = None,
) -> str:
    """Builds a token shaped like a real Cognito token for the given `token_use`:
    `token_use="access"` (the default) gets `client_id`/`scope`, never `aud`.
    `token_use="id"` gets `aud`, never `client_id`/`scope`. `token_use=None`
    omits the claim entirely (a generic, non-Cognito OIDC access token)."""
    now = int(time.time())
    claims: dict = {"sub": subject, "iss": issuer, "iat": now, "exp": now + exp_delta}
    if token_use is not None:
        claims["token_use"] = token_use
    if token_use == "id":
        if audience is not None:
            claims["aud"] = audience
    else:
        if client_id is not None:
            claims["client_id"] = client_id
        if scope is not None:
            claims["scope"] = scope
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, signing_key, algorithm="RS256")


def _provider(**overrides) -> OIDCAuthProvider:
    defaults = dict(
        issuer=ISSUER, jwks_client=_FakeJWKSClient(_signing_key.public_key()), client_id=CLIENT_ID,
        required_scopes=frozenset({"commitments.read"}),
    )
    defaults.update(overrides)
    return OIDCAuthProvider(**defaults)


# ---- 1. valid JWT accepted ---------------------------------------------------------------------

def test_valid_jwt_is_accepted():
    provider = _provider()
    claims = provider.authenticate(AuthRequest(authorization_header=f"Bearer {_token()}"))
    assert claims.subject == "user-abc"
    assert claims.auth_method == "oidc"
    assert "commitments.read" in claims.scopes


# ---- Cognito access-token validation (no `aud` claim at all) -----------------------------------

def test_normal_cognito_access_token_with_no_aud_claim_is_accepted():
    """The core regression: a real Cognito access token has NO `aud` claim
    whatsoever — only `client_id`. A provider that (incorrectly) asks PyJWT to
    verify `audience=<client_id>` rejects every genuine Cognito access token
    outright. This must succeed."""
    token = _token(token_use="access", client_id=CLIENT_ID, audience=None)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert "aud" not in claims  # sanity: this really is Cognito-shaped

    resolved = _provider(client_id=CLIENT_ID).authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
    assert resolved.subject == "user-abc"


def test_access_token_with_wrong_client_id_is_rejected():
    token = _token(client_id="some-other-app-client")
    with pytest.raises(InvalidToken):
        _provider(client_id=CLIENT_ID).authenticate(AuthRequest(authorization_header=f"Bearer {token}"))


def test_access_token_client_id_not_checked_when_provider_has_none_configured():
    provider = _provider(client_id=None, required_scopes=frozenset())
    token = _token(client_id="literally-anything")
    claims = provider.authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
    assert claims.subject == "user-abc"


def test_generic_oidc_access_token_with_no_token_use_claim_still_validates_client_id():
    """Non-Cognito OIDC providers don't set `token_use` at all — still treated
    as an access token (what a Bearer header conventionally carries)."""
    token = _token(token_use=None, client_id=CLIENT_ID)
    claims = _provider().authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
    assert claims.subject == "user-abc"


# ---- ID tokens: rejected by default, only accepted when explicitly allowed ---------------------

def test_id_token_is_rejected_by_default():
    """"Prefer access tokens for API/MCP authorization" — an ID token must not
    authenticate unless the deployment explicitly opts in."""
    id_token = _token(token_use="id", audience=CLIENT_ID, scope=None)
    with pytest.raises(InvalidToken):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {id_token}"))


def test_id_token_is_accepted_when_explicitly_allowed():
    id_token = _token(token_use="id", audience=CLIENT_ID, scope=None)
    provider = _provider(allow_id_tokens=True, required_scopes=frozenset())
    claims = provider.authenticate(AuthRequest(authorization_header=f"Bearer {id_token}"))
    assert claims.subject == "user-abc"
    assert claims.auth_method == "oidc"


def test_id_token_wrong_aud_is_rejected_even_when_allowed():
    id_token = _token(token_use="id", audience="some-other-app-client", scope=None)
    provider = _provider(allow_id_tokens=True, required_scopes=frozenset())
    with pytest.raises(InvalidToken):
        provider.authenticate(AuthRequest(authorization_header=f"Bearer {id_token}"))


def test_id_tokens_carry_no_oauth_scope_so_a_scope_requirement_rejects_them():
    """Reinforces "prefer access tokens": even an explicitly-allowed ID token
    can't satisfy a real `required_scopes` check, since Cognito ID tokens
    never carry an OAuth `scope` claim."""
    id_token = _token(token_use="id", audience=CLIENT_ID, scope=None)
    provider = _provider(allow_id_tokens=True, required_scopes=frozenset({"commitments.read"}))
    with pytest.raises(InsufficientScope):
        provider.authenticate(AuthRequest(authorization_header=f"Bearer {id_token}"))


# ---- 2. invalid signature rejected ---------------------------------------------------------------

def test_invalid_signature_is_rejected():
    """Signed with a different key than the one JWKS resolves to — the classic
    forged-token attack this whole mechanism exists to stop."""
    forged = _token(signing_key=_other_key)
    with pytest.raises(InvalidToken):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {forged}"))


def test_tampered_payload_is_rejected():
    token = _token()
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}A.{signature}"  # corrupt the payload, keep the original signature
    with pytest.raises(InvalidToken):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {tampered}"))


# ---- 3. expired JWT rejected ----------------------------------------------------------------------

def test_expired_jwt_is_rejected():
    expired = _token(exp_delta=-3600)
    with pytest.raises(TokenExpired):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {expired}"))


# ---- 4. wrong issuer rejected -----------------------------------------------------------------------

def test_wrong_issuer_is_rejected():
    wrong_issuer_token = _token(issuer="https://evil.example.com/not-cognito")
    with pytest.raises(InvalidToken):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {wrong_issuer_token}"))


# ---- 6. missing bearer token rejected --------------------------------------------------------------

def test_missing_authorization_header_is_rejected():
    with pytest.raises(AuthenticationRequired):
        _provider().authenticate(AuthRequest(authorization_header=None))


@pytest.mark.parametrize("bad_header", ["", "NotBearer abc123", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz"])
def test_malformed_authorization_header_is_rejected(bad_header):
    with pytest.raises(AuthenticationRequired):
        _provider().authenticate(AuthRequest(authorization_header=bad_header))


def test_query_string_tokens_are_never_supported():
    """AuthRequest has no field for a query-string token at all — the only
    credential surface is the Authorization header."""
    assert not hasattr(AuthRequest(), "access_token")
    assert not hasattr(AuthRequest(), "token")


# ---- 7. required scopes: ALL must be present, not just one (regression) ------------------------

def test_missing_required_scope_is_rejected():
    token = _token(scope="context.read")  # doesn't include the provider's required "commitments.read"
    with pytest.raises(InsufficientScope):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {token}"))


def test_having_only_some_of_several_required_scopes_is_rejected():
    """The regression this fix targets: requiring {A, B} must mean the token
    needs BOTH, not "at least one of them" (set-intersection semantics)."""
    provider = _provider(required_scopes=frozenset({"commitments.read", "commitments.write"}))
    token = _token(scope="commitments.read")  # only one of the two required scopes
    with pytest.raises(InsufficientScope):
        provider.authenticate(AuthRequest(authorization_header=f"Bearer {token}"))


def test_having_all_required_scopes_is_accepted():
    provider = _provider(required_scopes=frozenset({"commitments.read", "commitments.write"}))
    token = _token(scope="commitments.read commitments.write agent.execute")  # a superset, in any order
    claims = provider.authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
    assert {"commitments.read", "commitments.write"} <= claims.scopes


def test_no_required_scopes_configured_means_any_token_passes_the_scope_check():
    provider = _provider(required_scopes=frozenset())
    token = _token(scope="")
    claims = provider.authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
    assert claims.scopes == frozenset()


def test_unexpected_token_use_is_rejected():
    token = _token(token_use="refresh")
    with pytest.raises(InvalidToken):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {token}"))


# ---- 15. no token leakage in errors ----------------------------------------------------------------

def test_error_messages_never_contain_the_raw_token():
    token = _token(exp_delta=-3600)
    try:
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
        pytest.fail("expected TokenExpired")
    except TokenExpired as exc:
        assert token not in str(exc)

    forged = _token(signing_key=_other_key)
    try:
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {forged}"))
        pytest.fail("expected InvalidToken")
    except InvalidToken as exc:
        assert forged not in str(exc)


# ---- 13. local auth mode works -----------------------------------------------------------------

def test_local_auth_provider_returns_deterministic_dev_identity():
    provider = LocalAuthProvider(default_user_id="usr_dev_default", default_workspace_id="ws_dev_default")
    claims = provider.authenticate(AuthRequest())
    assert claims.subject == "local:usr_dev_default"
    assert claims.auth_method == "local"
    assert claims.dev_workspace_hint == "ws_dev_default"


def test_local_auth_provider_ignores_the_authorization_header_entirely():
    """Local mode never checks a bearer token — that's the whole point."""
    provider = LocalAuthProvider(default_user_id="usr_dev_default", default_workspace_id="ws_dev_default")
    claims = provider.authenticate(AuthRequest(authorization_header="Bearer not-even-a-real-jwt"))
    assert claims.subject == "local:usr_dev_default"
