from __future__ import annotations

from typing import Any

from promise_agent import llm
from promise_agent.context_retrieval import ContextRetriever, DocumentSearchProvider, MessageSearchProvider, build_commitment_query
from promise_agent.document_revision import build_revised_document
from promise_agent.steps import approval as approval_step
from promise_agent.steps import completion as completion_step
from promise_agent.steps import extraction as extraction_step
from promise_domain.enums import ApprovalStatus, IntegrationStatus
from promise_domain.models import (
    Action,
    AgentRun,
    Approval,
    AuditEvent,
    Commitment,
    Contact,
    IntegrationAccount,
)
from promise_shared.errors import DocumentArtifactMissing, NotFoundError, VerifiedContactRequiredError, WorkspaceAccessError
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


def _require_owned_commitment(ctx: AppContext, *, workspace_id: str, commitment_id: str, user_id: str) -> Commitment:
    """A personal commitment resource is user-owned, not just workspace-owned —
    workspace membership alone is not enough (see the Identity & Authorization
    spec). Raises `WorkspaceAccessError` (403) whether the commitment belongs
    to a different user in this same workspace, giving no way to tell that
    case apart from any other authorization failure by response shape alone."""
    commitment = ctx.repos.commitments.require(workspace_id, commitment_id)
    if commitment.user_id != user_id:
        raise WorkspaceAccessError("commitment", commitment_id)
    return commitment


def _owned_commitment_ids(ctx: AppContext, *, workspace_id: str, user_id: str) -> set[str]:
    """The join `list_actions`/`list_pending_approvals` need to scope personal
    resources that carry no owner field of their own — one indexed
    `list_user_owned` call, never a per-row lookup, so this stays a bounded
    join, not N+1, and never a workspace-wide read of Commitment either."""
    return {c.id for c in ctx.repos.commitments.list_user_owned(workspace_id, user_id)}


def _owned_action_ids(ctx: AppContext, *, workspace_id: str, user_id: str) -> set[str]:
    owned_commitment_ids = _owned_commitment_ids(ctx, workspace_id=workspace_id, user_id=user_id)
    return {
        a.id for a in ctx.repos.actions.list(workspace_id)
        if a.commitment_id is None or a.commitment_id in owned_commitment_ids
    }


def _require_owned_action(ctx: AppContext, *, workspace_id: str, action: Action, user_id: str) -> None:
    """Actions/approvals carry no owner field of their own (see the domain
    model) — ownership is derived transitively through the commitment an
    action is for (`Action.commitment_id`), never a new field on Action or
    Approval. An action with no linked commitment isn't gated this way —
    "where applicable", per the spec."""
    if not action.commitment_id:
        return
    commitment = ctx.repos.commitments.get(workspace_id, action.commitment_id)
    if commitment is not None and commitment.user_id != user_id:
        raise WorkspaceAccessError("action", action.id)


def get_commitment(ctx: AppContext, *, workspace_id: str, user_id: str, commitment_id: str) -> Commitment:
    return _require_owned_commitment(ctx, workspace_id=workspace_id, commitment_id=commitment_id, user_id=user_id)


def search_commitments(
    ctx: AppContext, *, workspace_id: str, user_id: str, query: str = "", status: str | None = None
) -> list[Commitment]:
    """Workspace AND user scoped: a commitment is a personal resource (see
    `_require_owned_commitment`), so listing must never hand back another
    user's commitments just because they're in the same workspace. Goes
    through `Repository.list_user_owned` (the indexed path, backed by the
    `UserOwnedIndex` GSI in production) rather than `Repository.list` --
    never a workspace-wide read followed by a Python `user_id` filter, here
    or in any transport layer. See that method's docstring."""
    rows = ctx.repos.commitments.list_user_owned(workspace_id, user_id)
    if status:
        rows = [r for r in rows if r.status.value == status]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in f"{r.title} {r.description}".lower()]
    return sorted(rows, key=lambda r: r.due_at or "9999")


UPDATABLE_COMMITMENT_FIELDS = {"title", "description", "due_at", "priority", "status", "contact_id"}


def update_commitment(ctx: AppContext, *, workspace_id: str, user_id: str, commitment_id: str, **fields: Any) -> Commitment:
    unknown = set(fields) - UPDATABLE_COMMITMENT_FIELDS
    if unknown:
        raise ValueError(f"cannot update fields: {sorted(unknown)}")
    _require_owned_commitment(ctx, workspace_id=workspace_id, commitment_id=commitment_id, user_id=user_id)

    def mutate(c: Commitment) -> None:
        for key, value in fields.items():
            setattr(c, key, value)

    return ctx.repos.commitments.update(workspace_id, commitment_id, mutate)


def complete_commitment(ctx: AppContext, *, workspace_id: str, user_id: str, commitment_id: str) -> Commitment:
    _require_owned_commitment(ctx, workspace_id=workspace_id, commitment_id=commitment_id, user_id=user_id)
    return completion_step.complete_commitment(commitment_id, workspace_id, ctx.agent_repos)


def cancel_commitment(ctx: AppContext, *, workspace_id: str, user_id: str, commitment_id: str) -> Commitment:
    _require_owned_commitment(ctx, workspace_id=workspace_id, commitment_id=commitment_id, user_id=user_id)
    return completion_step.cancel_commitment(commitment_id, workspace_id, ctx.agent_repos)


def retrieve_commitment_context(
    ctx: AppContext, *, workspace_id: str, user_id: str, commitment_id: str, limit: int | None = None
) -> dict[str, Any]:
    """Ranked, explainable documents/messages relevant to completing a commitment
    (`promise_agent.context_retrieval`). Read-only — never mutates the commitment.

    Enforces both workspace scoping (via the workspace-scoped repository lookup)
    and ownership (`user_id` must match the commitment's own `user_id`) before
    running retrieval, so this never becomes a way to read another user's or
    another workspace's context by guessing a commitment id.
    """
    commitment = ctx.repos.commitments.require(workspace_id, commitment_id)
    if commitment.user_id != user_id:
        raise WorkspaceAccessError("commitment", commitment_id)
    contact = ctx.repos.contacts.get(workspace_id, commitment.contact_id) if commitment.contact_id else None

    query = build_commitment_query(commitment, contact, user_id=user_id, limit=limit)
    providers = [DocumentSearchProvider(ctx.integrations.get()), MessageSearchProvider(ctx.integrations.get())]
    gmail = ctx.integrations.resolve_for_user(workspace_id, user_id, provider_name="gmail")
    if gmail is not None:
        providers.append(MessageSearchProvider(gmail))
    retriever = ContextRetriever(ctx.integrations.get(), providers=providers)
    outcome = retriever.retrieve(query)

    return {
        "commitment": commitment,
        "results": outcome.items,
        "status": outcome.status,
        "errors": outcome.errors,
        "request_id": outcome.request_id,
        "ranking_strategy": outcome.ranking_strategy,
    }


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


def get_document_artifact(ctx: AppContext, *, workspace_id: str, document_id: str) -> dict[str, Any]:
    """The *binary* artifact for a `Document` (see that model's own docstring for
    why this is distinct from `content_text`) -- download/attachment use, never
    retrieval/search (`get_file` above covers that). Ownership: `Document` is a
    workspace-shared resource, the same as `Contact`/`get_file` -- not a
    per-user one (it has no `user_id` field, deliberately not added just to
    make this check possible; see the README's "Resource ownership" section) --
    so `ctx.repos.documents.require(workspace_id, ...)` is already the full
    ownership check, exactly like every other workspace-scoped lookup.
    A document with no binary artifact at all (`storage_key` unset) or one
    whose artifact is missing from the blob store raises `DocumentArtifactMissing`
    -- never a fabricated/empty response."""
    document = ctx.repos.documents.require(workspace_id, document_id)
    if not document.storage_key:
        raise DocumentArtifactMissing(document_id)
    blob = ctx.blob_store.get(document.storage_key)
    if blob is None:
        raise DocumentArtifactMissing(document_id)
    return {"data": blob.data, "content_type": blob.content_type, "filename": blob.filename, "size_bytes": blob.size_bytes}


def search_messages(ctx: AppContext, *, workspace_id: str, user_id: str, query: str) -> list[dict[str, Any]]:
    """Local/demo messages plus, when the calling user has their own connected
    Gmail account, that account's Gmail search results merged in. Gmail
    participation is user-scoped by construction (`IntegrationRegistry.
    resolve_for_user` looks the account up by `workspace_id` + `user_id`,
    the authenticated principal — never a caller-supplied account id), so
    User A's search never surfaces User B's Gmail messages even though the
    endpoint itself is otherwise workspace-level. A Gmail failure here
    propagates as the classified `IntegrationError` it is (see
    `promise_shared.errors`) — this endpoint returns a bare list, so there is
    no partial-result shape to quietly fall back into the way
    `ContextRetriever` has for the agent's own retrieval step."""
    results = ctx.integrations.get().search_messages(workspace_id, query)
    gmail = ctx.integrations.resolve_for_user(workspace_id, user_id, provider_name="gmail")
    if gmail is not None:
        results = results + gmail.search_messages(workspace_id, query)
    return results


def get_message(ctx: AppContext, *, workspace_id: str, user_id: str, message_id: str) -> dict[str, Any]:
    message = ctx.integrations.get().get_message(workspace_id, message_id)
    if message is None:
        gmail = ctx.integrations.resolve_for_user(workspace_id, user_id, provider_name="gmail")
        if gmail is not None:
            message = gmail.get_message(workspace_id, message_id)
    if message is None:
        raise NotFoundError("message", message_id)
    return message


# ---- drafting / revision ----------------------------------------------------

def prepare_revision(ctx: AppContext, *, workspace_id: str, document_id: str, feedback: str) -> dict[str, Any]:
    """The manual, on-demand counterpart to `SendRevisedDocumentPlanner`'s own
    revision step -- both go through `build_revised_document` (packages/agent)
    so a document revised here carries the exact same real binary artifact
    (never just `content_text`) a document revised by the agent does; see
    that function's own docstring."""
    source = get_file(ctx, workspace_id=workspace_id, file_id=document_id)
    revised_text, changes = llm.revise_document(source.get("content_text", ""), feedback)
    revised_doc = build_revised_document(
        workspace_id=workspace_id, source_doc=source, revised_text=revised_text, changes=changes,
        documents=ctx.repos.documents, blob_store=ctx.blob_store,
    )
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
    _require_owned_commitment(ctx, workspace_id=workspace_id, commitment_id=commitment_id, user_id=user_id)
    return ctx.orchestrator.run_handle_commitment(workspace_id, user_id, commitment_id, trigger=trigger)


def request_approval(ctx: AppContext, *, workspace_id: str, user_id: str, action_id: str) -> Approval:
    """Manual out-of-band approval request (distinct from the orchestrator's own
    call into `approval_step.request_approval` during `handle_commitment`, which
    is already gated by that function's own `_require_owned_commitment` check).
    Same ownership rule as `decide_approval`/`execute_approved_action`: `Action`
    carries no owner field of its own, so ownership is derived transitively
    through the commitment the action is for."""
    action = ctx.repos.actions.require(workspace_id, action_id)
    _require_owned_action(ctx, workspace_id=workspace_id, action=action, user_id=user_id)
    return approval_step.request_approval(action_id, workspace_id, ctx.agent_repos)


def decide_approval(
    ctx: AppContext, *, workspace_id: str, approval_id: str, decision: str, decided_by: str, note: str | None = None
) -> Approval:
    approval = ctx.repos.approvals.require(workspace_id, approval_id)
    action = ctx.repos.actions.require(workspace_id, approval.action_id)
    _require_owned_action(ctx, workspace_id=workspace_id, action=action, user_id=decided_by)
    return approval_step.decide_approval(approval_id, workspace_id, ApprovalStatus(decision), decided_by, ctx.agent_repos, note)


def execute_approved_action(ctx: AppContext, *, workspace_id: str, action_id: str, actor: str) -> dict[str, Any]:
    action = ctx.repos.actions.require(workspace_id, action_id)
    _require_owned_action(ctx, workspace_id=workspace_id, action=action, user_id=actor)
    return ctx.orchestrator.execute_approved_action(workspace_id, action_id, actor=actor)


def list_pending_approvals(ctx: AppContext, *, workspace_id: str, user_id: str) -> list[Approval]:
    """`Approval` has no owner field of its own (see `_require_owned_action`) —
    scoped transitively through the action's commitment, same as the
    single-resource `decide_approval` check, so listing and acting agree on
    who can see what."""
    owned_action_ids = _owned_action_ids(ctx, workspace_id=workspace_id, user_id=user_id)
    return [
        a for a in ctx.repos.approvals.list(workspace_id)
        if a.status == ApprovalStatus.PENDING and a.action_id in owned_action_ids
    ]


def list_actions(ctx: AppContext, *, workspace_id: str, user_id: str, commitment_id: str | None = None) -> list[Action]:
    """When `commitment_id` is given, this is effectively a single-resource
    lookup — an unowned commitment_id is rejected outright (`WorkspaceAccessError`,
    same as `get_commitment`), never silently returned as an empty list. With no
    `commitment_id`, this is a list operation: it's narrowed to the caller's own
    commitments' actions, not rejected."""
    if commitment_id:
        _require_owned_commitment(ctx, workspace_id=workspace_id, commitment_id=commitment_id, user_id=user_id)
        return [a for a in ctx.repos.actions.list(workspace_id) if a.commitment_id == commitment_id]

    owned_commitment_ids = _owned_commitment_ids(ctx, workspace_id=workspace_id, user_id=user_id)
    return [
        a for a in ctx.repos.actions.list(workspace_id)
        if a.commitment_id is None or a.commitment_id in owned_commitment_ids
    ]


def list_agent_runs(ctx: AppContext, *, workspace_id: str, user_id: str) -> list[AgentRun]:
    """`AgentRun` already carries its own `user_id` (unlike Action/Approval) —
    no transitive lookup needed, same field `get_agent_run` checks."""
    return sorted(ctx.repos.agent_runs.list(workspace_id, user_id=user_id), key=lambda r: r.started_at, reverse=True)


def get_agent_run(ctx: AppContext, *, workspace_id: str, user_id: str, agent_run_id: str) -> dict[str, Any]:
    run = ctx.repos.agent_runs.require(workspace_id, agent_run_id)
    if run.user_id != user_id:
        # AgentRun already carries its own user_id (unlike Action/Approval) — no
        # transitive lookup needed.
        raise WorkspaceAccessError("agent_run", agent_run_id)
    steps = sorted(
        [s for s in ctx.repos.agent_steps.list(workspace_id) if s.agent_run_id == agent_run_id],
        key=lambda s: s.started_at,
    )
    return {"run": run, "steps": steps}


def list_audit_events(ctx: AppContext, *, workspace_id: str) -> list[AuditEvent]:
    """Deliberately workspace-scoped, not user-filtered — not an oversight.
    `AuditEvent.actor` can be a real user_id OR `"system"`/`"agent"` for
    automated steps, and a single event's `entity_type` spans commitments,
    actions, approvals, etc., each with different ownership; there's no one
    consistent "owner" to filter by. It's a workspace-level compliance/
    observability log — who did what, when, across the whole workspace — the
    same shape a real product's audit trail has, not a personal resource. See
    the README's Identity & Authorization section for the full reasoning."""
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


def list_integration_accounts(ctx: AppContext, *, workspace_id: str, user_id: str) -> list[IntegrationAccount]:
    """User-owned resource (`IntegrationAccount.user_id`, same as Commitment) —
    scoped by both workspace_id and user_id via the indexed `list_user_owned`
    path (the `UserOwnedIndex` GSI in production), not by loading every
    workspace account and filtering here, in the router, or with `Repository.
    list`'s plain workspace-wide query."""
    return ctx.repos.integration_accounts.list_user_owned(workspace_id, user_id)


def _require_owned_integration_account(ctx: AppContext, *, workspace_id: str, account_id: str, user_id: str) -> IntegrationAccount:
    account = ctx.repos.integration_accounts.require(workspace_id, account_id)
    if account.user_id != user_id:
        raise WorkspaceAccessError("integration_account", account_id)
    return account


def disconnect_integration_account(ctx: AppContext, *, workspace_id: str, user_id: str, account_id: str) -> IntegrationAccount:
    _require_owned_integration_account(ctx, workspace_id=workspace_id, account_id=account_id, user_id=user_id)
    return ctx.repos.integration_accounts.update(
        workspace_id, account_id, lambda a: setattr(a, "status", IntegrationStatus.DISCONNECTED)
    )

