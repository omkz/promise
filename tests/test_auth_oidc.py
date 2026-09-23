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
network access required (see `_FakeJWKSClient`)."""

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL"
AUDIENCE = "test-client-id"

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
    *, subject: str = "user-abc", issuer: str = ISSUER, audience: str | None = AUDIENCE, scope: str = "commitments.read",
    token_use: str | None = "access", exp_delta: int = 3600, signing_key=_signing_key, extra_claims: dict | None = None,
) -> str:
    now = int(time.time())
    claims: dict = {"sub": subject, "iss": issuer, "iat": now, "exp": now + exp_delta}
    if audience is not None:
        claims["aud"] = audience
    if token_use is not None:
        claims["token_use"] = token_use
    if scope is not None:
        claims["scope"] = scope
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, signing_key, algorithm="RS256")


def _provider(**overrides) -> OIDCAuthProvider:
    defaults = dict(
        issuer=ISSUER, jwks_client=_FakeJWKSClient(_signing_key.public_key()), audience=AUDIENCE,
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


# ---- 5. wrong audience rejected ------------------------------------------------------------------

def test_wrong_audience_is_rejected():
    wrong_audience_token = _token(audience="some-other-client-id")
    with pytest.raises(InvalidToken):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {wrong_audience_token}"))


def test_audience_not_checked_when_provider_has_none_configured():
    """Some OIDC providers/token types legitimately have no single audience to
    check (e.g. Cognito access tokens) — only enforced when configured."""
    provider = _provider(audience=None, required_scopes=frozenset())
    token = _token(audience="anything-at-all")
    claims = provider.authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
    assert claims.subject == "user-abc"


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


# ---- 7. missing required scope rejected -----------------------------------------------------------

def test_missing_required_scope_is_rejected():
    token = _token(scope="context.read")  # doesn't include the provider's required "commitments.read"
    with pytest.raises(InsufficientScope):
        _provider().authenticate(AuthRequest(authorization_header=f"Bearer {token}"))


def test_no_required_scopes_configured_means_any_token_passes_the_scope_check():
    provider = _provider(required_scopes=frozenset())
    token = _token(scope="")
    claims = provider.authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
    assert claims.scopes == frozenset()


def test_token_use_id_and_access_are_both_accepted_by_default():
    for use in ("access", "id"):
        token = _token(token_use=use)
        claims = _provider().authenticate(AuthRequest(authorization_header=f"Bearer {token}"))
        assert claims.subject == "user-abc"


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
