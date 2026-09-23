from __future__ import annotations

import re
from typing import Any

from promise_agent import llm
from promise_agent.steps import approval as approval_step
from promise_agent.steps import completion as completion_step
from promise_agent.steps import extraction as extraction_step
from promise_domain.enums import ApprovalStatus, IntegrationStatus
from promise_domain.models import (
    Action,
    AgentRun,
    AgentStep,
    Approval,
    AuditEvent,
    Commitment,
    Contact,
    Document,
    IntegrationAccount,
)
from promise_shared.errors import NotFoundError, VerifiedContactRequiredError
from promise_shared.ids import new_id

from .bootstrap import AppContext

"""Application services shared by every channel (REST, MCP, and any future client).

This module is the seam the target architecture calls out explicitly:
"Both REST endpoints and MCP tools should call the same application/domain
services." Neither `services/api` nor `services/mcp` re-implements any of
this logic — they only translate transport <-> these functions.
"""


# ---- commitments -----------------------------------------------------------

def create_commitment(
    ctx: AppContext, *, workspace_id: str, user_id: str, text: str, source_system: str = "web",
    source_ref: str | None = None, occurred_at: str | None = None, confirm: bool = False,
) -> dict[str, Any]:
    """Detect a commitment in `text` and, when it clears the auto-capture confidence
    bar, persist it with provenance. If the detector isn't confident enough, nothing
    is persisted and the result carries `needs_confirmation=True` instead — re-call
    with `confirm=True` to capture it anyway. If the text isn't a personal commitment
    at all, nothing is persisted and the result carries `detected=False`.

    `occurred_at` is the source/utterance's own timestamp when the caller has one (an
    Alexa transcript time, an email's send time, ...) — kept distinct from the
    `CommitmentSource.created_at` PROMISE always stamps at processing time."""
    result = extraction_step.extract_commitment(
        text, workspace_id, user_id, ctx.agent_repos, source_system=source_system, source_ref=source_ref,
        occurred_at=occurred_at, confirm=confirm,
    )
    return result


def get_commitment(ctx: AppContext, *, workspace_id: str, commitment_id: str) -> Commitment:
    return ctx.repos.commitments.require(workspace_id, commitment_id)


def search_commitments(ctx: AppContext, *, workspace_id: str, query: str = "", status: str | None = None) -> list[Commitment]:
    rows = ctx.repos.commitments.list(workspace_id)
    if status:
        rows = [r for r in rows if r.status.value == status]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in f"{r.title} {r.description}".lower()]
    return sorted(rows, key=lambda r: r.due_at or "9999")


UPDATABLE_COMMITMENT_FIELDS = {"title", "description", "due_at", "priority", "status", "contact_id"}


def update_commitment(ctx: AppContext, *, workspace_id: str, commitment_id: str, **fields: Any) -> Commitment:
    unknown = set(fields) - UPDATABLE_COMMITMENT_FIELDS
    if unknown:
        raise ValueError(f"cannot update fields: {sorted(unknown)}")

    def mutate(c: Commitment) -> None:
        for key, value in fields.items():
            setattr(c, key, value)

    return ctx.repos.commitments.update(workspace_id, commitment_id, mutate)


def complete_commitment(ctx: AppContext, *, workspace_id: str, commitment_id: str) -> Commitment:
    return completion_step.complete_commitment(commitment_id, workspace_id, ctx.agent_repos)


def cancel_commitment(ctx: AppContext, *, workspace_id: str, commitment_id: str) -> Commitment:
    return completion_step.cancel_commitment(commitment_id, workspace_id, ctx.agent_repos)


def list_contacts(ctx: AppContext, *, workspace_id: str) -> list[Contact]:
    return ctx.repos.contacts.list(workspace_id)


# ---- integration-backed search/read (never touches Gmail/Drive directly) --

def search_files(ctx: AppContext, *, workspace_id: str, query: str) -> list[dict[str, Any]]:
    return ctx.integrations.get().search_files(workspace_id, query)


def get_file(ctx: AppContext, *, workspace_id: str, file_id: str) -> dict[str, Any]:
    file = ctx.integrations.get().get_file(workspace_id, file_id)
    if file is None:
        raise NotFoundError("document", file_id)
    return file


def search_messages(ctx: AppContext, *, workspace_id: str, query: str) -> list[dict[str, Any]]:
    return ctx.integrations.get().search_messages(workspace_id, query)


def get_message(ctx: AppContext, *, workspace_id: str, message_id: str) -> dict[str, Any]:
    message = ctx.integrations.get().get_message(workspace_id, message_id)
    if message is None:
        raise NotFoundError("message", message_id)
    return message


# ---- drafting / revision ----------------------------------------------------

def prepare_revision(ctx: AppContext, *, workspace_id: str, document_id: str, feedback: str) -> dict[str, Any]:
    source = get_file(ctx, workspace_id=workspace_id, file_id=document_id)
    revised_text, changes = llm.revise_document(source.get("content_text", ""), feedback)
    revised_doc = Document(
        id=new_id("doc"),
        workspace_id=workspace_id,
        name=re.sub(r"(\.[^.]+)$", r"_revised\1", source["name"]),
        type=source.get("type", "text/plain"),
        content_text=revised_text,
        metadata={"derived_from": document_id, "changes": changes, "agent_generated": True},
    )
    ctx.repos.documents.save(revised_doc)
    return {"document": revised_doc, "changes": changes}


def create_draft(ctx: AppContext, *, workspace_id: str, contact_id: str, document_id: str) -> dict[str, Any]:
    contact = ctx.repos.contacts.require(workspace_id, contact_id)
    if not contact.email:
        # Never send to a fabricated address (e.g. the contact's name) — stop with a clear,
        # actionable error instead.
        raise VerifiedContactRequiredError(contact_id=contact.id, contact_name=contact.name)
    document = get_file(ctx, workspace_id=workspace_id, file_id=document_id)
    draft = ctx.integrations.get().create_draft(
        workspace_id,
        recipient=contact.email,
        subject="Revised proposal",
        body=f"Hi {contact.name},\n\nPlease find attached the revised proposal.\n\nBest,\nPROMISE",
        attachment_file_id=document["id"],
    )
    return draft


# ---- agent run / approval / execution --------------------------------------

def handle_commitment(ctx: AppContext, *, workspace_id: str, user_id: str, commitment_id: str, trigger: str = "handle_commitment") -> dict[str, Any]:
    return ctx.orchestrator.run_handle_commitment(workspace_id, user_id, commitment_id, trigger=trigger)


def request_approval(ctx: AppContext, *, workspace_id: str, action_id: str) -> Approval:
    return approval_step.request_approval(action_id, workspace_id, ctx.agent_repos)


def decide_approval(
    ctx: AppContext, *, workspace_id: str, approval_id: str, decision: str, decided_by: str, note: str | None = None
) -> Approval:
    return approval_step.decide_approval(approval_id, workspace_id, ApprovalStatus(decision), decided_by, ctx.agent_repos, note)


def execute_approved_action(ctx: AppContext, *, workspace_id: str, action_id: str, actor: str) -> dict[str, Any]:
    return ctx.orchestrator.execute_approved_action(workspace_id, action_id, actor=actor)


def list_pending_approvals(ctx: AppContext, *, workspace_id: str) -> list[Approval]:
    return [a for a in ctx.repos.approvals.list(workspace_id) if a.status == ApprovalStatus.PENDING]


def list_actions(ctx: AppContext, *, workspace_id: str, commitment_id: str | None = None) -> list[Action]:
    rows = ctx.repos.actions.list(workspace_id)
    if commitment_id:
        rows = [r for r in rows if r.commitment_id == commitment_id]
    return rows


def list_agent_runs(ctx: AppContext, *, workspace_id: str) -> list[AgentRun]:
    return sorted(ctx.repos.agent_runs.list(workspace_id), key=lambda r: r.started_at, reverse=True)


def get_agent_run(ctx: AppContext, *, workspace_id: str, agent_run_id: str) -> dict[str, Any]:
    run = ctx.repos.agent_runs.require(workspace_id, agent_run_id)
    steps = sorted(
        [s for s in ctx.repos.agent_steps.list(workspace_id) if s.agent_run_id == agent_run_id],
        key=lambda s: s.started_at,
    )
    return {"run": run, "steps": steps}


def list_audit_events(ctx: AppContext, *, workspace_id: str) -> list[AuditEvent]:
    return sorted(ctx.repos.audit_events.list(workspace_id), key=lambda e: e.created_at, reverse=True)


# ---- integration accounts (connections) ------------------------------------

def connect_integration_account(
    ctx: AppContext, *, workspace_id: str, user_id: str, provider: str, account_identifier: str, scopes: list[str] | None = None
) -> IntegrationAccount:
    """Record a connected account. Real OAuth token exchange/storage happens outside
    this function (e.g. in a secrets manager) — only non-secret metadata lands here."""
    account = IntegrationAccount(
        id=new_id("ia"),
        workspace_id=workspace_id,
        user_id=user_id,
        provider=provider,
        account_identifier=account_identifier,
        status=IntegrationStatus.CONNECTED,
        scopes=scopes or [],
    )
    ctx.repos.integration_accounts.save(account)
    return account


def list_integration_accounts(ctx: AppContext, *, workspace_id: str) -> list[IntegrationAccount]:
    return ctx.repos.integration_accounts.list(workspace_id)


def disconnect_integration_account(ctx: AppContext, *, workspace_id: str, account_id: str) -> IntegrationAccount:
    return ctx.repos.integration_accounts.update(
        workspace_id, account_id, lambda a: setattr(a, "status", IntegrationStatus.DISCONNECTED)
    )

