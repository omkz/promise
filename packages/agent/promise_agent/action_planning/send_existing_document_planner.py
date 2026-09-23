from __future__ import annotations

from typing import Any

from promise_domain.enums import ActionStatus, ActionType
from promise_domain.models import Action, Commitment, Contact, Document
from promise_shared.errors import VerifiedContactRequiredError
from promise_shared.ids import new_id

from ..context import AgentRepos
from ..context_retrieval import ContextItem, ContextItemType
from . import _signals
from .message_composer import DeterministicMessageComposer, MessageComposer
from .planner import PlanningError
from .schema import ActionPlan

""""Send a document as it already exists" — the other of the two concrete
planners v1 ships (see `send_message_planner.py` for the "send a *revised*
document" case). A generic "send/email/share" commitment with no revision
language (no "revised"/"updated"/"incorporate feedback"/...) belongs here,
never to `SendRevisedDocumentPlanner` — this planner never calls
`llm.revise_document` and never creates a new `Document` row; it attaches the
existing document exactly as retrieval found it.
"""


class SendExistingDocumentPlanner:
    """Prepares an outbound draft attaching an existing document to a "send X
    to contact" commitment (no revision requested), and proposes a
    `SEND_MESSAGE` `Action` — never sends it, never requests approval, never
    completes the commitment."""

    name = "send_existing_document"

    def __init__(self, *, composer: MessageComposer | None = None) -> None:
        self._composer = composer or DeterministicMessageComposer()

    def supports(self, commitment: Commitment) -> bool:
        return _signals.has_send_verb(commitment.action) and not _signals.has_revision_signal(commitment)

    def plan(
        self, commitment: Commitment, contact: Contact | None, context_items: list[ContextItem],
        *, repos: AgentRepos, agent_run_id: str,
    ) -> dict[str, Any]:
        document_items = [i for i in context_items if i.type == ContextItemType.DOCUMENT]
        if not document_items:
            raise PlanningError("No relevant document found for this commitment")
        if contact is None or not contact.email:
            # PROMISE never invents a contact email (e.g. from a first name) or sends to a
            # placeholder address — an external send action stops here until a verified
            # email is on file for this contact.
            raise VerifiedContactRequiredError(
                contact_id=contact.id if contact else None, contact_name=contact.name if contact else None
            )

        top_document = document_items[0]
        # Go through the IntegrationProvider abstraction, not `repos.documents` directly
        # (this planner must work the same way against a future non-local provider) — then
        # adapt the raw dict into a `Document` so callers get the same attribute-access
        # shape `SendRevisedDocumentPlanner` already returns.
        raw_doc = repos.integration.get_file(commitment.workspace_id, top_document.source.source_id)
        if raw_doc is None:
            raise PlanningError(f"Relevant document '{top_document.source.source_id}' could not be loaded")
        document = Document.model_validate(raw_doc)

        message = self._composer.compose_existing_document(commitment=commitment, contact=contact, document_name=document.name)
        draft = repos.integration.create_draft(
            commitment.workspace_id, recipient=contact.email, subject=message.subject, body=message.body,
            attachment_file_id=document.id,
        )

        action_plan = ActionPlan.for_action_type(
            ActionType.SEND_MESSAGE,
            summary=f'Send "{document.name}" to {contact.name}',
            rationale=(
                f"Commitment action \"{commitment.action}\" matches a plain send-type workflow (no revision "
                f"requested); selected top-ranked document '{top_document.title}' from context retrieval."
            ),
            target_contact_id=contact.id,
            supporting_context_ids=[top_document.id],
            payload={"draft_id": draft["id"], "document_id": document.id},
        )

        action = Action(
            id=new_id("act"),
            workspace_id=commitment.workspace_id,
            commitment_id=commitment.id,
            agent_run_id=agent_run_id,
            type=action_plan.action_type,
            status=ActionStatus.PROPOSED,
            payload=action_plan.payload,
            idempotency_key=f"send_message:{draft['id']}",
        )
        repos.actions.save(action)

        # `changes` stays `[]`: nothing was revised. Kept for shape-compatibility with the
        # generic orchestrator, which currently reads `document`/`changes`/`draft`/`action`
        # off whichever planner ran (see the Action Planning Engine README limitation note).
        return {"action_plan": action_plan, "document": document, "changes": [], "draft": draft, "action": action}
