from __future__ import annotations

from .authorization import can, permissions_for_role, require
from .oidc import OIDCAuthProvider, discover_jwks_url
from .principal import AuthenticatedPrincipal, Permission
from .provider import AuthProvider, AuthRequest, LocalAuthProvider, TokenClaims

__all__ = [
    "AuthProvider",
    "AuthRequest",
    "AuthenticatedPrincipal",
    "LocalAuthProvider",
    "OIDCAuthProvider",
    "Permission",
    "TokenClaims",
    "can",
    "discover_jwks_url",
    "permissions_for_role",
    "require",
]
