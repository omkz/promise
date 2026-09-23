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

Cognito access vs. ID tokens (important, and easy to get wrong): a Cognito
*access* token has a `client_id` claim and NO `aud` claim at all. A Cognito
*ID* token has the opposite — an `aud` claim (set to the app client id) and
no OAuth `scope`. Blindly telling PyJWT to verify `audience=<client_id>` on
every token — the naive approach — rejects every real Cognito access token
outright, because PyJWT then requires an `aud` claim that access tokens never
carry. So this provider never asks PyJWT to verify `aud` itself; instead it
decodes with `verify_aud=False` and checks `client_id`/`aud` itself, against
the *correct* claim for the token's own `token_use`:

- `token_use=access` (the default, preferred path — "prefer access tokens
  for API/MCP authorization"): checks `client_id` against the configured
  `client_id`, when one is configured.
- `token_use=id`: only accepted when `allow_id_tokens=True` (off by default);
  checks the standard `aud` claim against `client_id` instead.
- anything else (or a non-Cognito provider that omits `token_use` entirely):
  treated as an access token, since that's what a Bearer header conventionally
  carries — its audience, if configured, is still checked as `client_id` first,
  falling back to `aud` for OIDC providers that use that claim on access tokens.
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
    `token_use`-correct audience/client_id (see module docstring), and
    required scopes. Compatible with Amazon Cognito User Pools and any
    standard OIDC provider.

    Never falls back to `LocalAuthProvider` on failure — every failure raises
    a specific, controlled error. Fails closed.
    """

    auth_method = "oidc"

    def __init__(
        self, *, issuer: str, jwks_client: JWKSClient, client_id: str | None = None, allow_id_tokens: bool = False,
        required_scopes: frozenset[str] = frozenset(), algorithms: tuple[str, ...] = ("RS256",),
    ) -> None:
        self._issuer = issuer
        self._jwks_client = jwks_client
        self._client_id = client_id
        self._allow_id_tokens = allow_id_tokens
        self._required_scopes = required_scopes
        self._algorithms = list(algorithms)

    @classmethod
    def from_config(
        cls, *, issuer: str, client_id: str | None, jwks_url: str | None, required_scopes: frozenset[str] = frozenset(),
        allow_id_tokens: bool = False,
    ) -> "OIDCAuthProvider":
        """Production factory: builds a real `jwt.PyJWKClient`, discovering the
        JWKS URL via OIDC discovery when one isn't explicitly configured."""
        resolved_jwks_url = jwks_url or discover_jwks_url(issuer)
        return cls(
            issuer=issuer, jwks_client=jwt.PyJWKClient(resolved_jwks_url), client_id=client_id,
            required_scopes=required_scopes, allow_id_tokens=allow_id_tokens,
        )

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
                # Never verify `aud` here: a Cognito access token has no `aud` claim at
                # all, and PyJWT would reject every one of them if asked to. Audience is
                # checked explicitly below, against the claim that's actually correct
                # for this token's own token_use.
                options={"require": ["exp", "iss", "sub"], "verify_aud": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpired() from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidToken(f"unexpected issuer: {exc}") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise InvalidToken(f"token missing required claim: {exc}") from exc
        except jwt.PyJWTError as exc:
            # Signature mismatch, malformed token, unsupported algorithm, etc. — all
            # collapse to the same InvalidToken; PyJWT already refused to trust the
            # payload, so nothing here reads a claim from it.
            raise InvalidToken(f"token validation failed: {exc}") from exc

        self._validate_token_use_and_audience(claims)

        scopes = _extract_scopes(claims)
        if not self._required_scopes.issubset(scopes):
            missing = sorted(self._required_scopes - scopes)
            raise InsufficientScope(f"token missing required scope(s): {missing}")

        subject = claims.get("sub")
        if not subject:
            raise InvalidToken("token missing 'sub' claim")

        return TokenClaims(subject=subject, scopes=scopes, auth_method="oidc")

    def _validate_token_use_and_audience(self, claims: dict) -> None:
        """See the module docstring for why access and ID tokens are checked
        against different claims (`client_id` vs. `aud`), never both blindly."""
        token_use = claims.get("token_use")

        if token_use == "id":
            if not self._allow_id_tokens:
                raise InvalidToken(
                    "ID tokens are not accepted for API/MCP authorization here — pass an access token "
                    "(or configure allow_id_tokens=True to opt in)"
                )
            if self._client_id is not None and not _claim_matches(claims.get("aud"), self._client_id):
                raise InvalidToken("unexpected audience")
            return

        if token_use is not None and token_use != "access":
            raise InvalidToken(f"unexpected token_use: {token_use!r}")

        # token_use == "access", or absent entirely (a non-Cognito OIDC access token
        # conventionally has no token_use claim at all — still treated as an access
        # token, since that's what a Bearer header carries by convention).
        if self._client_id is not None:
            candidate = claims.get("client_id")
            if candidate is None:
                candidate = claims.get("aud")  # some non-Cognito access tokens use `aud` instead
            if not _claim_matches(candidate, self._client_id):
                raise InvalidToken("unexpected client_id")

    @staticmethod
    def _extract_bearer_token(authorization_header: str | None) -> str:
        if not authorization_header:
            raise AuthenticationRequired("missing Authorization header")
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationRequired("Authorization header must be 'Bearer <token>'")
        return token.strip()


def _claim_matches(value: object, expected: str) -> bool:
    if isinstance(value, list):
        return expected in value
    return value == expected


def _extract_scopes(claims: dict) -> frozenset[str]:
    scope_claim = claims.get("scope")
    if isinstance(scope_claim, str) and scope_claim:
        return frozenset(scope_claim.split())
    scp_claim = claims.get("scp")  # some providers (Cognito custom scopes) use `scp` as a list
    if isinstance(scp_claim, list):
        return frozenset(str(s) for s in scp_claim)
    return frozenset()
