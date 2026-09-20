from __future__ import annotations

import os
from dataclasses import dataclass

from promise_agent.context import AgentRepos
from promise_agent.orchestrator import AgentOrchestrator
from promise_domain.models import User, Workspace
from promise_integrations.local_provider import LocalIntegrationProvider
from promise_integrations.registry import IntegrationRegistry
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
    default_workspace_id: str
    default_user_id: str


def ensure_default_workspace(repos: RepoSet) -> None:
    """Local development may use a default dev workspace/user; nothing else in the
    codebase is allowed to assume a single global workspace exists."""
    if repos.workspaces.get(DEFAULT_WORKSPACE_ID) is None:
        repos.workspaces.save(Workspace(id=DEFAULT_WORKSPACE_ID, name="Dev Workspace", slug=DEFAULT_WORKSPACE_SLUG, is_default=True))
    if repos.users.get(DEFAULT_WORKSPACE_ID, DEFAULT_USER_ID) is None:
        repos.users.save(User(id=DEFAULT_USER_ID, workspace_id=DEFAULT_WORKSPACE_ID, email=DEFAULT_USER_EMAIL, name="Dev User"))


def build_context(store: EntityStore | None = None) -> AppContext:
    store = store or build_store()
    repos = build_repo_set(store)

    local_provider = LocalIntegrationProvider(repos.documents, repos.messages, repos.drafts)
    integrations = IntegrationRegistry(local_provider)

    agent_repos = build_agent_repos(repos, integrations.get())
    orchestrator = AgentOrchestrator(agent_repos)

    ensure_default_workspace(repos)

    return AppContext(
        store=store,
        repos=repos,
        agent_repos=agent_repos,
        integrations=integrations,
        orchestrator=orchestrator,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        default_user_id=DEFAULT_USER_ID,
    )
