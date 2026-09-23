from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Permission(str, Enum):
    """Deliberately small — not a giant permission matrix. Covers the
    workspace-owned surfaces the Identity & Authorization spec calls out."""

    COMMITMENTS_READ = "commitments.read"
    COMMITMENTS_WRITE = "commitments.write"
    CONTEXT_READ = "context.read"
    AGENT_EXECUTE = "agent.execute"
    ACTIONS_APPROVE = "actions.approve"
    INTEGRATIONS_MANAGE = "integrations.manage"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The one thing every application-service call is authorized against.

    `user_id`/`workspace_id` are never taken directly off a request (header,
    query param, JSON body, MCP tool argument, or even the JWT's own claims) —
    they're the *output* of identity resolution: JWT `subject` -> domain
    `User` -> `WorkspaceMembership` -> this workspace, specifically. See
    `promise_app.identity.resolve_principal`, the only place that's allowed to
    construct one from raw auth input.
    """

    subject: str
    """The verified external identity: JWT `sub` claim in oidc mode, or
    `"local:<user_id>"` in local mode (never used as a trust boundary itself —
    `user_id` below is what every downstream check actually uses)."""
    user_id: str
    workspace_id: str
    role: str
    """`MembershipRole` value for `user_id` in `workspace_id` — informs `permissions`."""
    permissions: frozenset[Permission]
    scopes: frozenset[str]
    """Raw OAuth scopes from the token, kept for observability/audit — authorization
    decisions use `permissions` (role-derived), not these directly."""
    auth_method: str
    """`"local"` or `"oidc"` — never silently substituted for the other; see
    `promise_auth.provider`."""

    def has_permission(self, permission: Permission) -> bool:
        return permission in self.permissions
