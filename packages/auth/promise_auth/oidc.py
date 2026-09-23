from __future__ import annotations

import json
import urllib.request
from typing import Protocol

import jwt
from promise_shared.errors import AuthenticationRequired, InsufficientScope, InvalidToken, TokenExpired

from .provider import AuthRequest, TokenClaims

"""Cognito User Pool / OIDC-compatible bearer-token authentication.

Every failure mode is a distinct, caught exception mapped to one of this
project's controlled `PromiseError`s — never a bare `except Exception: return
None`-style swallow, and the token's signature is *always* verified against
JWKS before any claim in it is trusted (see SECURITY in the Identity &
Authorization Foundation spec: "Do NOT decode JWTs without signature
verification").
"""


class SigningKey(Protocol):
    key: object


class JWKSClient(Protocol):
    """What `OIDCAuthProvider` needs from a JWKS source — `jwt.PyJWKClient`
    satisfies this in production; tests inject a fake with no network access."""

    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


def discover_jwks_url(issuer: str, *, timeout: float = 5.0) -> str:
    """OIDC discovery (`{issuer}/.well-known/openid-configuration` -> `jwks_uri`),
    preferred over hard-coding a JWKS endpoint. Falls back to the Cognito User
    Pool convention (`{issuer}/.well-known/jwks.json`) if discovery is
    unreachable — Cognito issuers don't actually serve a discovery document at
    that exact path in every region/setup, so this keeps `COGNITO_JWKS_URL`
    optional without hard failing when discovery isn't available.
    """
    base = issuer.rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/.well-known/openid-configuration", timeout=timeout) as resp:  # noqa: S310
            doc = json.loads(resp.read())
            jwks_uri = doc.get("jwks_uri")
            if jwks_uri:
                return jwks_uri
    except Exception:  # noqa: BLE001 - discovery is best-effort, never fatal
        pass
    return f"{base}/.well-known/jwks.json"


class OIDCAuthProvider:
    """`AUTH_MODE=oidc`: validates a `Bearer` access token per standard JWT
    validation principles — signature (via JWKS), issuer, expiration,
    audience (when configured), `token_use`, and required scopes. Compatible
    with Amazon Cognito User Pools and any standard OIDC provider.

    Never falls back to `LocalAuthProvider` on failure — every failure raises
    a specific, controlled error (see module docstring). Fails closed.
    """

    auth_method = "oidc"

    def __init__(
        self, *, issuer: str, jwks_client: JWKSClient, audience: str | None = None,
        required_scopes: frozenset[str] = frozenset(), algorithms: tuple[str, ...] = ("RS256",),
        allowed_token_use: tuple[str, ...] = ("access", "id"),
    ) -> None:
        self._issuer = issuer
        self._jwks_client = jwks_client
        self._audience = audience
        self._required_scopes = required_scopes
        self._algorithms = list(algorithms)
        self._allowed_token_use = allowed_token_use

    @classmethod
    def from_config(
        cls, *, issuer: str, audience: str | None, jwks_url: str | None, required_scopes: frozenset[str] = frozenset(),
    ) -> "OIDCAuthProvider":
        """Production factory: builds a real `jwt.PyJWKClient`, discovering the
        JWKS URL via OIDC discovery when one isn't explicitly configured."""
        resolved_jwks_url = jwks_url or discover_jwks_url(issuer)
        return cls(issuer=issuer, jwks_client=jwt.PyJWKClient(resolved_jwks_url), audience=audience, required_scopes=required_scopes)

    def authenticate(self, request: AuthRequest) -> TokenClaims:
        token = self._extract_bearer_token(request.authorization_header)

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        except Exception as exc:  # noqa: BLE001 - any JWKS/key-resolution failure is an invalid token, not a crash
            raise InvalidToken(f"could not resolve a signing key for this token: {exc}") from exc

        try:
            claims = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iss", "sub"], "verify_aud": self._audience is not None},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpired() from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidToken(f"unexpected issuer: {exc}") from exc
        except jwt.InvalidAudienceError as exc:
            raise InvalidToken(f"unexpected audience: {exc}") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise InvalidToken(f"token missing required claim: {exc}") from exc
        except jwt.PyJWTError as exc:
            # Signature mismatch, malformed token, unsupported algorithm, etc. — all
            # collapse to the same InvalidToken; PyJWT already refused to trust the
            # payload, so nothing here reads a claim from it.
            raise InvalidToken(f"token validation failed: {exc}") from exc

        token_use = claims.get("token_use")
        if token_use is not None and token_use not in self._allowed_token_use:
            raise InvalidToken(f"unexpected token_use: {token_use!r}")

        scopes = _extract_scopes(claims)
        if self._required_scopes and not (self._required_scopes & scopes):
            raise InsufficientScope(f"token missing required scope(s): {sorted(self._required_scopes)}")

        subject = claims.get("sub")
        if not subject:
            raise InvalidToken("token missing 'sub' claim")

        return TokenClaims(subject=subject, scopes=scopes, auth_method="oidc")

    @staticmethod
    def _extract_bearer_token(authorization_header: str | None) -> str:
        if not authorization_header:
            raise AuthenticationRequired("missing Authorization header")
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationRequired("Authorization header must be 'Bearer <token>'")
        return token.strip()


def _extract_scopes(claims: dict) -> frozenset[str]:
    scope_claim = claims.get("scope")
    if isinstance(scope_claim, str) and scope_claim:
        return frozenset(scope_claim.split())
    scp_claim = claims.get("scp")  # some providers (Cognito custom scopes) use `scp` as a list
    if isinstance(scp_claim, list):
        return frozenset(str(s) for s in scp_claim)
    return frozenset()
