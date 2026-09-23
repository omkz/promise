from __future__ import annotations

from promise_shared.errors import InsufficientScope

from .principal import AuthenticatedPrincipal, Permission

"""Role -> permission mapping and the `can`/`require` authorization helpers.

Deliberately not a giant permission matrix: two roles (see
`promise_domain.enums.MembershipRole`), six permissions. Every
workspace-owned application-service call goes through `require()` (or the
weaker `can()` for read-time branching) rather than re-deriving its own
notion of "is this allowed" — see `services/api/promise_api/deps.py` and
`services/mcp/promise_mcp/server.py`.

Workspace *membership itself* (does this principal have any standing in this
workspace at all) is checked earlier, during principal resolution — see
`promise_app.identity.resolve_principal`, which raises `WorkspaceAccessDenied`
before a principal with no membership ever reaches this module. This module
only ever decides "given a membership this principal definitely has, is this
specific action allowed for their role" — `can(principal, permission)` is the
`can(principal, workspace, action)` the spec asks for, with `workspace`
already baked into `principal` by that earlier resolution step.
"""

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "owner": frozenset(Permission),  # every permission
    "member": frozenset(
        {
            Permission.COMMITMENTS_READ,
            Permission.COMMITMENTS_WRITE,
            Permission.CONTEXT_READ,
            Permission.AGENT_EXECUTE,
        }
    ),
    # Deliberately excludes actions.approve and integrations.manage for members —
    # approving a side effect and connecting/disconnecting an integration stay
    # owner-only. Keep this table short; do not grow it into a matrix.
}


def permissions_for_role(role: str) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def can(principal: AuthenticatedPrincipal, permission: Permission) -> bool:
    return principal.has_permission(permission)


def require(principal: AuthenticatedPrincipal, permission: Permission) -> None:
    """Raises `InsufficientScope` — never silently no-ops — when the principal's
    role doesn't grant `permission` in the workspace it was resolved for."""
    if not can(principal, permission):
        raise InsufficientScope(
            f"role '{principal.role}' does not grant '{permission.value}' in workspace '{principal.workspace_id}'"
        )
