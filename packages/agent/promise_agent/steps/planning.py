from __future__ import annotations

import re

from promise_domain.enums import ActionStatus, ActionType
from promise_domain.models import Action, Commitment, Contact, Document
from promise_shared.errors import PromiseError, VerifiedContactRequiredError
from promise_shared.ids import new_id

from .. import llm
from ..context import AgentRepos
from ..context_retrieval import ContextItem, ContextItemType


class PlanningError(PromiseError):
    pass


def plan_send_revised_document(
    commitment: Commitment,
    contact: Contact | None,
    context_items: list[ContextItem],
    repos: AgentRepos,
    agent_run_id: str,
) -> dict:
    """Plan + prepare a "send the revised document" action for a commitment.

    Produces a revised Document, a Draft (via the integration provider),
    and a proposed Action of type SEND_MESSAGE — but never sends anything;
    execution only happens after explicit approval.

    `context_items` are the ranked, structured `ContextItem`s from
    `promise_agent.context_retrieval` (best match first) — never a giant
    string. Only their `source.source_id` is used to load full content via
    `get_file`/`get_message`; the items themselves carry only small snippets
    (see DOCUMENT CONTENT in the Context Retrieval Engine spec).
    """
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

    source_doc = repos.integration.get_file(commitment.workspace_id, document_items[0].source.source_id)
    if source_doc is None:
        raise PlanningError(f"Relevant document '{document_items[0].source.source_id}' could not be loaded")

    message_items = [i for i in context_items if i.type == ContextItemType.MESSAGE]
    feedback = "Apply the latest available feedback."
    if message_items:
        top_message = repos.integration.get_message(commitment.workspace_id, message_items[0].source.source_id)
        if top_message and top_message.get("content"):
            feedback = top_message["content"]

    revised_text, changes = llm.revise_document(source_doc.get("content_text", ""), feedback)
    revised_doc = Document(
        id=new_id("doc"),
        workspace_id=commitment.workspace_id,
        name=re.sub(r"(\.[^.]+)$", r"_revised\1", source_doc["name"]),
        type=source_doc.get("type", "text/plain"),
        content_text=revised_text,
        metadata={"derived_from": source_doc["id"], "changes": changes, "agent_generated": True},
    )
    repos.documents.save(revised_doc)

    draft = repos.integration.create_draft(
        commitment.workspace_id,
        recipient=contact.email,
        subject="Revised proposal",
        body=f"Hi {contact.name},\n\nPlease find attached the revised proposal.\n\nBest,\nPROMISE",
        attachment_file_id=revised_doc.id,
    )

    action = Action(
        id=new_id("act"),
        workspace_id=commitment.workspace_id,
        commitment_id=commitment.id,
        agent_run_id=agent_run_id,
        type=ActionType.SEND_MESSAGE,
        status=ActionStatus.PROPOSED,
        payload={"draft_id": draft["id"], "document_id": revised_doc.id},
        idempotency_key=f"send_message:{draft['id']}",
    )
    repos.actions.save(action)

    return {"document": revised_doc, "changes": changes, "draft": draft, "action": action}
