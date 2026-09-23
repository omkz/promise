from __future__ import annotations

from dataclasses import dataclass

from promise_agent.context import AgentRepos
from promise_domain.models import (
    Action,
    AgentRun,
    AgentStep,
    Approval,
    AuditEvent,
    Commitment,
    CommitmentSource,
    Contact,
    Document,
    Draft,
    IntegrationAccount,
    Message,
    User,
    Workspace,
    WorkspaceMembership,
)
from promise_domain.repository import Repository
from promise_shared.store import EntityStore


class WorkspaceRepository:
    """Workspace is the root tenant entity, so it can't be scoped *by* a workspace_id
    the way every other repository is. It self-scopes: a workspace's partition key is
    its own id. This is the one deliberate exception to "every repository call takes
    a workspace_id" in the codebase.
    """

    _ENTITY = "workspace"

    def __init__(self, store: EntityStore) -> None:
        self._store = store

    def save(self, workspace: Workspace) -> Workspace:
        row = workspace.model_dump(mode="json")
        row["workspace_id"] = workspace.id
        self._store.put(self._ENTITY, row)
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        row = self._store.get(self._ENTITY, workspace_id, workspace_id)
        return Workspace.model_validate(row) if row else None

    def list(self) -> list[Workspace]:
        return [Workspace.model_validate(r) for r in self._store.query_all(self._ENTITY)]


@dataclass
class RepoSet:
    workspaces: WorkspaceRepository
    users: Repository[User]
    memberships: Repository[WorkspaceMembership]
    contacts: Repository[Contact]
    documents: Repository[Document]
    messages: Repository[Message]
    commitments: Repository[Commitment]
    sources: Repository[CommitmentSource]
    actions: Repository[Action]
    drafts: Repository[Draft]
    approvals: Repository[Approval]
    integration_accounts: Repository[IntegrationAccount]
    agent_runs: Repository[AgentRun]
    agent_steps: Repository[AgentStep]
    audit_events: Repository[AuditEvent]


def build_repo_set(store: EntityStore) -> RepoSet:
    return RepoSet(
        workspaces=WorkspaceRepository(store),
        users=Repository(store, "user", User),
        memberships=Repository(store, "workspace_membership", WorkspaceMembership),
        contacts=Repository(store, "contact", Contact),
        documents=Repository(store, "document", Document),
        messages=Repository(store, "message", Message),
        commitments=Repository(store, "commitment", Commitment),
        sources=Repository(store, "commitment_source", CommitmentSource),
        actions=Repository(store, "action", Action),
        drafts=Repository(store, "draft", Draft),
        approvals=Repository(store, "approval", Approval),
        integration_accounts=Repository(store, "integration_account", IntegrationAccount),
        agent_runs=Repository(store, "agent_run", AgentRun),
        agent_steps=Repository(store, "agent_step", AgentStep),
        audit_events=Repository(store, "audit_event", AuditEvent),
    )


def build_agent_repos(repos: RepoSet, integration) -> AgentRepos:
    return AgentRepos(
        commitments=repos.commitments,
        sources=repos.sources,
        contacts=repos.contacts,
        documents=repos.documents,
        messages=repos.messages,
        actions=repos.actions,
        drafts=repos.drafts,
        approvals=repos.approvals,
        agent_runs=repos.agent_runs,
        agent_steps=repos.agent_steps,
        audit_events=repos.audit_events,
        integration=integration,
    )
