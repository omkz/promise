from __future__ import annotations

import re
from typing import Any

from promise_domain.enums import ActionStatus, ActionType
from promise_domain.models import Action, Commitment, Contact, Document
from promise_shared.blobs import blob_ref
from promise_shared.errors import VerifiedContactRequiredError
from promise_shared.ids import new_id

from .. import llm
from ..context import AgentRepos
from ..context_retrieval import ContextItem, ContextItemType
from . import _signals
from .document_artifacts import generate_revised_artifact
from .message_composer import DeterministicMessageComposer, MessageComposer
from .planner import PlanningError
from .schema import ActionPlan

""""Send a *revised* document to a contact" — one of the two concrete planners
v1 ships (see `send_existing_document_planner.py` for the plain "send it as
it already exists" case). Nothing Andi/Sarah/proposal-specific is hard-coded
here: `supports()` recognizes the *general shape* of the commitment (a
send-type action verb AND a revision signal — see `action_planning._signals`),
and `plan()` selects whichever document/message `ContextItem`s the retrieval
engine ranked highest for *this* commitment. A commitment about a different
contact and a different document works identically; see
`tests/test_action_planning.py::
test_planner_selection_is_not_hard_coded_to_any_specific_name_or_file`.

Only this planner ever calls `llm.revise_document` — a generic "send/email/
share" commitment with no revision language routes to
`SendExistingDocumentPlanner` instead and never touches the LLM at all.
"""


class SendRevisedDocumentPlanner:
    """Prepares a *revised* document + outbound draft for a "send the revised/
    updated X to contact" commitment, and proposes a `SEND_MESSAGE` `Action` —
    never sends it, never requests approval, never completes the commitment."""

    name = "send_revised_document"

    def __init__(self, *, composer: MessageComposer | None = None) -> None:
        self._composer = composer or DeterministicMessageComposer()

    def supports(self, commitment: Commitment) -> bool:
        return _signals.has_send_verb(commitment.action) and _signals.has_revision_signal(commitment)

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
        source_doc = repos.integration.get_file(commitment.workspace_id, top_document.source.source_id)
        if source_doc is None:
            raise PlanningError(f"Relevant document '{top_document.source.source_id}' could not be loaded")

        message_items = [i for i in context_items if i.type == ContextItemType.MESSAGE]
        feedback = "Apply the latest available feedback."
        top_message = message_items[0] if message_items else None
        if top_message:
            loaded_message = repos.integration.get_message(commitment.workspace_id, top_message.source.source_id)
            if loaded_message and loaded_message.get("content"):
                feedback = loaded_message["content"]

        # Full content is loaded here, only for the items actually selected — everything
        # upstream of this (ranking, filtering) worked off small ContextItem snippets only.
        revised_text, changes = llm.revise_document(source_doc.get("content_text", ""), feedback)

        # Two separate concerns, deliberately: `revised_text` (extracted text, for
        # retrieval/AI/search — persisted as `content_text` below) and the binary
        # artifact (the actual downloadable/attachable file). `generate_revised_artifact`
        # produces a real, valid artifact matching the source document's own format
        # (a real DOCX via python-docx for a DOCX source; the revised text's own UTF-8
        # bytes, under its own real content type, otherwise) — never plain text renamed
        # with a misleading extension. The artifact is written to the blob store keyed
        # by the document's own id (`blob_ref`) before the `Document` row is saved, so
        # `storage_key`/`artifact_size_bytes` are correct from the first write, never a
        # separate update after the fact.
        document_id = new_id("doc")
        artifact_bytes, artifact_content_type = generate_revised_artifact(
            source_type=source_doc.get("type", "text/plain"), revised_text=revised_text
        )
        storage_key = blob_ref(commitment.workspace_id, document_id)
        revised_doc_name = re.sub(r"(\.[^.]+)$", r"_revised\1", source_doc["name"])
        repos.blob_store.put(storage_key, artifact_bytes, content_type=artifact_content_type, filename=revised_doc_name)

        revised_doc = Document(
            id=document_id,
            workspace_id=commitment.workspace_id,
            name=revised_doc_name,
            type=artifact_content_type,
            content_text=revised_text,
            storage_key=storage_key,
            artifact_size_bytes=len(artifact_bytes),
            metadata={"derived_from": source_doc["id"], "changes": changes, "agent_generated": True},
        )
        repos.documents.save(revised_doc)

        message = self._composer.compose(commitment=commitment, contact=contact, changes=changes)
        draft = repos.integration.create_draft(
            commitment.workspace_id, recipient=contact.email, subject=message.subject, body=message.body,
            attachment_file_id=revised_doc.id,
        )

        supporting_ids = [top_document.id] + ([top_message.id] if top_message else [])
        action_plan = ActionPlan.for_action_type(
            ActionType.SEND_MESSAGE,
            summary=f'Send revised "{revised_doc.name}" to {contact.name}',
            rationale=(
                f"Commitment action \"{commitment.action}\" matches a send-a-revision workflow; "
                f"selected top-ranked document '{top_document.title}'"
                + (f" and message '{top_message.title}' for feedback" if top_message else "")
                + " from context retrieval."
            ),
            target_contact_id=contact.id,
            supporting_context_ids=supporting_ids,
            payload={"draft_id": draft["id"], "document_id": revised_doc.id},
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

        return {"action_plan": action_plan, "document": revised_doc, "changes": changes, "draft": draft, "action": action}


# Backward-compatible name: every earlier caller/test refers to this planner as
# `SendMessagePlanner` (from before "send existing" vs. "send revised" were split
# into separate planners). Same class, same object identity — not a copy.
SendMessagePlanner = SendRevisedDocumentPlanner
