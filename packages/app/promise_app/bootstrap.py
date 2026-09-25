from __future__ import annotations

import os
from dataclasses import dataclass

from promise_agent.context import AgentRepos
from promise_agent.orchestrator import AgentOrchestrator
from promise_auth import AuthProvider, LocalAuthProvider, OIDCAuthProvider
from promise_domain.enums import MembershipRole, MembershipStatus
from promise_domain.models import IntegrationAccount, User, Workspace, WorkspaceMembership
from promise_integrations.gmail import GmailIntegrationProvider, gmail_enabled, load_gmail_config
from promise_integrations.local_provider import LocalIntegrationProvider
from promise_integrations.registry import IntegrationRegistry
from promise_shared.ids import new_id
from promise_shared.secrets import SecretStore, build_secret_store
from promise_shared.store import EntityStore, build_store

from .repos import RepoSet, build_agent_repos, build_repo_set

DEFAULT_WORKSPACE_ID = "ws_dev_default"
DEFAULT_WORKSPACE_SLUG = "dev"
DEFAULT_USER_ID = "usr_dev_default"
DEFAULT_USER_EMAIL = "dev@promise.local"


@dataclass
class AppContext:
    store: EntityStore
    repos: RepoSet
    agent_repos: AgentRepos
    integrations: IntegrationRegistry
    orchestrator: AgentOrchestrator
    auth_provider: AuthProvider
    secret_store: SecretStore
    default_workspace_id: str
    default_user_id: str


def ensure_default_workspace(repos: RepoSet) -> None:
    """Local development may use a default dev workspace/user/membership;
    nothing else in the codebase is allowed to assume a single global
    workspace exists."""
    if repos.workspaces.get(DEFAULT_WORKSPACE_ID) is None:
        repos.workspaces.save(Workspace(id=DEFAULT_WORKSPACE_ID, name="Dev Workspace", slug=DEFAULT_WORKSPACE_SLUG, is_default=True))
    if repos.users.get(DEFAULT_WORKSPACE_ID, DEFAULT_USER_ID) is None:
        repos.users.save(
            User(
                id=DEFAULT_USER_ID, workspace_id=DEFAULT_WORKSPACE_ID, email=DEFAULT_USER_EMAIL, name="Dev User",
                external_subject=f"local:{DEFAULT_USER_ID}",
            )
        )
    existing_membership = next(
        (m for m in repos.memberships.list(DEFAULT_WORKSPACE_ID) if m.user_id == DEFAULT_USER_ID), None
    )
    if existing_membership is None:
        repos.memberships.save(
            WorkspaceMembership(
                id=new_id("mem"), workspace_id=DEFAULT_WORKSPACE_ID, user_id=DEFAULT_USER_ID,
                role=MembershipRole.OWNER, status=MembershipStatus.ACTIVE,
            )
        )


def build_auth_provider() -> AuthProvider:
    """`AUTH_MODE=local` (the default, for `uv run pytest` / local `uvicorn` with
    no Cognito user pool) -> `LocalAuthProvider`. `AUTH_MODE=oidc` ->
    `OIDCAuthProvider`, requiring `COGNITO_ISSUER` at minimum. No mode ever
    silently falls back to another — see SECURITY in the Identity &
    Authorization Foundation spec."""
    mode = os.getenv("AUTH_MODE", "local").lower()
    if mode == "oidc":
        issuer = os.getenv("COGNITO_ISSUER")
        if not issuer:
            raise RuntimeError("AUTH_MODE=oidc requires COGNITO_ISSUER to be set")
        required_scopes = frozenset(s.strip() for s in os.getenv("COGNITO_REQUIRED_SCOPES", "").split(",") if s.strip())
        return OIDCAuthProvider.from_config(
            issuer=issuer,
            client_id=os.getenv("COGNITO_CLIENT_ID") or os.getenv("COGNITO_AUDIENCE") or None,
            jwks_url=os.getenv("COGNITO_JWKS_URL") or None,
            required_scopes=required_scopes,
            # Off by default: "prefer access tokens for API/MCP authorization" — ID
            # tokens (which carry no OAuth scope) are only accepted when explicitly
            # opted into.
            allow_id_tokens=os.getenv("COGNITO_ALLOW_ID_TOKENS", "false").lower() == "true",
        )
    if mode != "local":
        raise RuntimeError(f"unknown AUTH_MODE {mode!r} (expected 'local' or 'oidc')")
    return LocalAuthProvider(
        default_user_id=os.getenv("DEV_USER_ID", DEFAULT_USER_ID),
        default_workspace_id=os.getenv("DEV_WORKSPACE_ID", DEFAULT_WORKSPACE_ID),
    )


def _gmail_provider_factory(repos: RepoSet, secret_store: SecretStore):
    """Closure, not a method: `IntegrationRegistry.resolve_for_user` calls this
    with one already-resolved, already-ownership-checked `IntegrationAccount`
    per call, so it only ever needs to close over the (fixed, process-wide)
    `repos`/`secret_store`, never take them as call-site arguments a caller
    could get wrong."""

    def factory(account: IntegrationAccount):
        from promise_shared.errors import IntegrationNotConnected

        if not account.secret_ref:
            raise IntegrationNotConnected("gmail")
        return GmailIntegrationProvider(
            account_id=account.id,
            workspace_id=account.workspace_id,
            secret_ref=account.secret_ref,
            secret_store=secret_store,
            config=load_gmail_config(),
            drafts=repos.drafts,
        )

    return factory


def build_context(
    store: EntityStore | None = None, *, auth_provider: AuthProvider | None = None, secret_store: SecretStore | None = None
) -> AppContext:
    store = store or build_store()
    repos = build_repo_set(store)
    secret_store = secret_store or build_secret_store()

    local_provider = LocalIntegrationProvider(repos.documents, repos.messages, repos.drafts)
    # GMAIL_ENABLED=false (the default): no factory registered at all, so
    # resolve_for_user("gmail") always returns None and every Gmail-aware call
    # site behaves exactly as it did before Gmail existed -- zero behavior
    # change for anyone not opting in.
    provider_factories = {"gmail": _gmail_provider_factory(repos, secret_store)} if gmail_enabled() else {}
    integrations = IntegrationRegistry(local_provider, integration_accounts=repos.integration_accounts, provider_factories=provider_factories)

    agent_repos = build_agent_repos(repos, integrations)
    orchestrator = AgentOrchestrator(agent_repos)

    ensure_default_workspace(repos)

    return AppContext(
        store=store,
        repos=repos,
        agent_repos=agent_repos,
        integrations=integrations,
        orchestrator=orchestrator,
        auth_provider=auth_provider or build_auth_provider(),
        secret_store=secret_store,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        default_user_id=DEFAULT_USER_ID,
    )
