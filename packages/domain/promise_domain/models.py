from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from promise_shared.clock import iso_now

from .enums import (
    ActionStatus,
    ActionType,
    AgentRunStatus,
    AgentStepName,
    AgentStepStatus,
    ApprovalStatus,
    CommitmentStatus,
    DraftStatus,
    IntegrationStatus,
    Priority,
)


class Workspace(BaseModel):
    id: str
    name: str
    slug: str
    is_default: bool = False
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)


class User(BaseModel):
    id: str
    workspace_id: str
    email: str
    name: str
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)


class Contact(BaseModel):
    id: str
    workspace_id: str
    name: str
    email: str | None = None
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)
    deleted_at: str | None = None


class Document(BaseModel):
    id: str
    workspace_id: str
    name: str
    type: str
    content_text: str
    storage_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)
    deleted_at: str | None = None


class Message(BaseModel):
    id: str
    workspace_id: str
    sender: str
    recipient: str
    subject: str
    content: str
    occurred_at: str = Field(default_factory=iso_now)
    created_at: str = Field(default_factory=iso_now)


class CommitmentSource(BaseModel):
    """Provenance for a detected commitment. Answers "why do you think I promised this?"."""

    id: str
    workspace_id: str
    commitment_id: str
    source_type: str
    """e.g. 'conversation', 'email', 'alexa_transcript', 'manual'."""
    source_system: str
    """e.g. 'alexa', 'web', 'gmail'."""
    source_id: str
    excerpt: str
    occurred_at: str = Field(default_factory=iso_now)
    confidence: float = 1.0
    created_at: str = Field(default_factory=iso_now)


class Commitment(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    action: str
    title: str
    description: str
    contact_id: str | None = None
    due_at: str | None = None
    priority: Priority = Priority.MEDIUM
    status: CommitmentStatus = CommitmentStatus.OPEN
    confidence: float = 1.0
    source_id: str | None = None
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)
    completed_at: str | None = None
    cancelled_at: str | None = None
    version: int = 1


class Draft(BaseModel):
    id: str
    workspace_id: str
    action_id: str | None = None
    recipient: str
    subject: str
    body: str
    attachment_document_id: str | None = None
    status: DraftStatus = DraftStatus.DRAFT
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)
    sent_at: str | None = None


class Action(BaseModel):
    """A proposed side effect (send email, publish, modify a document, ...).

    Every external side effect flows through an Action so it can be gated
    behind an Approval and made idempotent via idempotency_key.
    """

    id: str
    workspace_id: str
    commitment_id: str | None = None
    agent_run_id: str | None = None
    type: ActionType
    status: ActionStatus = ActionStatus.PROPOSED
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)
    executed_at: str | None = None


class Approval(BaseModel):
    id: str
    workspace_id: str
    action_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: str = Field(default_factory=iso_now)
    decided_by: str | None = None
    decided_at: str | None = None
    note: str | None = None


class IntegrationAccount(BaseModel):
    """A user-connected external account (Gmail, Drive, Slack, ...).

    Raw OAuth secrets never live here — only non-secret metadata. Real
    tokens belong in a secret store (e.g. AWS Secrets Manager); this model
    carries a `secret_ref` pointer to that location instead.
    """

    id: str
    workspace_id: str
    user_id: str
    provider: str
    account_identifier: str
    status: IntegrationStatus = IntegrationStatus.PENDING
    scopes: list[str] = Field(default_factory=list)
    secret_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)


class AgentStep(BaseModel):
    id: str
    workspace_id: str
    agent_run_id: str
    name: AgentStepName
    status: AgentStepStatus = AgentStepStatus.RUNNING
    tool_calls: list[str] = Field(default_factory=list)
    input_summary: str | None = None
    output_summary: str | None = None
    error: str | None = None
    started_at: str = Field(default_factory=iso_now)
    ended_at: str | None = None


class AgentRun(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    commitment_id: str | None = None
    trigger: str
    status: AgentRunStatus = AgentRunStatus.RUNNING
    action_ids: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    started_at: str = Field(default_factory=iso_now)
    ended_at: str | None = None


class AuditEvent(BaseModel):
    id: str
    workspace_id: str
    actor: str
    """user_id, or 'system'/'agent' for automated actions."""
    event_type: str
    entity_type: str
    entity_id: str
    agent_run_id: str | None = None
    action_id: str | None = None
    approval_id: str | None = None
    summary: str
    created_at: str = Field(default_factory=iso_now)
