from __future__ import annotations

from dataclasses import dataclass

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
    Message,
)
from promise_domain.repository import Repository
from promise_integrations.base import IntegrationProvider


@dataclass
class AgentRepos:
    """Everything the agent steps/orchestrator are allowed to touch.

    Steps never talk to the EntityStore directly — only through these
    typed, workspace-scoped repositories — so the model can never mutate
    the database except through deterministic, auditable code paths.
    """

    commitments: Repository[Commitment]
    sources: Repository[CommitmentSource]
    contacts: Repository[Contact]
    documents: Repository[Document]
    messages: Repository[Message]
    actions: Repository[Action]
    drafts: Repository[Draft]
    approvals: Repository[Approval]
    agent_runs: Repository[AgentRun]
    agent_steps: Repository[AgentStep]
    audit_events: Repository[AuditEvent]
    integration: IntegrationProvider
