from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from promise_shared.errors import AuthenticationRequired

"""The `AuthProvider` abstraction: turns raw, transport-supplied auth signal into
verified `TokenClaims` — nothing more. It deliberately does NOT resolve a
`user_id`/`workspace_id` (that requires a domain lookup — `User`,
`WorkspaceMembership` — which this package doesn't depend on; see
`promise_app.identity.resolve_principal`, which takes these `TokenClaims` and
does that resolution).

`AuthRequest` is plain data, not a framework request object, so this whole
package stays independent of FastAPI/MCP — `services/api/promise_api/deps.py`
and `services/mcp/promise_mcp/server.py` are the only places that ever
construct one, each from its own transport's raw headers.
"""


@dataclass(frozen=True)
class AuthRequest:
    authorization_header: str | None = None
    """The raw `Authorization` header value, e.g. `"Bearer <token>"`. The only
    input `OIDCAuthProvider` ever reads."""
    requested_workspace_id: str | None = None
    """Which workspace the caller wants to act in, e.g. from `X-Workspace-Id`.
    Mode-agnostic, but never trusted blindly in either mode: local mode uses it
    as-is (not a security boundary); oidc mode's principal resolution
    cross-checks it against the resolved user's real `WorkspaceMembership`
    rows and rejects it (`WorkspaceAccessDenied`) if there isn't an active one —
    see `promise_app.identity.resolve_principal`."""
    dev_user_id: str | None = None
    """Only ever read by `LocalAuthProvider` — never by `OIDCAuthProvider`, and
    never treated as authoritative outside `AUTH_MODE=local`. The one field
    that is genuinely local-dev-only: user identity is never taken from a
    header in oidc mode, full stop."""


@dataclass(frozen=True)
class TokenClaims:
    """What authentication alone produces. No workspace_id here on purpose —
    see the module docstring."""

    subject: str
    scopes: frozenset[str]
    auth_method: str
    dev_workspace_hint: str | None = field(default=None)
    """Only ever set by `LocalAuthProvider`, from `AuthRequest.dev_workspace_id` /
    the configured default — a convenience for local dev, not a claim to trust
    in oidc mode."""


class AuthProvider(Protocol):
    auth_method: str

    def authenticate(self, request: AuthRequest) -> TokenClaims:
        """Raises `promise_shared.errors.AuthenticationRequired` /
        `InvalidToken` / `TokenExpired` / `InsufficientScope` on failure —
        never returns claims for a caller it hasn't actually verified."""
        ...


class LocalAuthProvider:
    """`AUTH_MODE=local`: deterministic developer identity. Clearly NOT
    production authentication — no signature is checked, nothing is verified,
    it exists purely so `uv run pytest` and local `uvicorn` work without a
    running Cognito user pool. Isolated behind the same `AuthProvider`
    interface `OIDCAuthProvider` implements so nothing above this layer can
    tell (or needs to care) which one is active, other than by checking
    `auth_method` for observability.
    """

    auth_method = "local"

    def __init__(self, *, default_user_id: str, default_workspace_id: str) -> None:
        self._default_user_id = default_user_id
        self._default_workspace_id = default_workspace_id

    def authenticate(self, request: AuthRequest) -> TokenClaims:
        user_id = request.dev_user_id or self._default_user_id
        workspace_id = request.requested_workspace_id or self._default_workspace_id
        if not user_id:
            raise AuthenticationRequired("local auth mode has no default dev user configured")
        return TokenClaims(
            subject=f"local:{user_id}", scopes=frozenset({"*"}), auth_method="local", dev_workspace_hint=workspace_id
        )
