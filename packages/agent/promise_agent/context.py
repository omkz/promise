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
from promise_integrations.registry import IntegrationRegistry


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
    # The default/workspace-wide provider (local/demo data) -- used for create_draft/
    # get_file/get_message during planning, which stay provider-agnostic on purpose
    # (see send_message_planner.py): PROMISE's own Draft row is always the approval
    # boundary, regardless of which provider ends up sending it.
    integration: IntegrationProvider
    # For resolving a per-user provider (Gmail) at the two points that genuinely need
    # one: retrieval (steps/retrieval.py, augmenting the default provider's results)
    # and execution (steps/execution.py, choosing who actually sends). See
    # IntegrationRegistry.resolve_for_user.
    integration_registry: IntegrationRegistry
