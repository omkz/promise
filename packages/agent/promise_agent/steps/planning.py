from __future__ import annotations

import re

from promise_domain.enums import ActionStatus, ActionType
from promise_domain.models import Action, Commitment, Contact, Document
from promise_shared.errors import PromiseError
from promise_shared.ids import new_id

from .. import llm
from ..context import AgentRepos


class PlanningError(PromiseError):
    pass


def plan_send_revised_document(
    commitment: Commitment,
    contact: Contact | None,
    documents: list[dict],
    messages: list[dict],
    repos: AgentRepos,
    agent_run_id: str,
) -> dict:
    """Plan + prepare a "send the revised document" action for a commitment.

    Produces a revised Document, a Draft (via the integration provider),
    and a proposed Action of type SEND_MESSAGE — but never sends anything;
    execution only happens after explicit approval.
    """
    if not documents:
        raise PlanningError("No relevant document found for this commitment")

    source_doc = documents[0]
    feedback = messages[0]["content"] if messages else "Apply the latest available feedback."

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

    recipient = (contact.email or contact.name) if contact else "recipient@example.com"
    recipient_name = contact.name if contact else "there"
    draft = repos.integration.create_draft(
        commitment.workspace_id,
        recipient=recipient,
        subject="Revised proposal",
        body=f"Hi {recipient_name},\n\nPlease find attached the revised proposal.\n\nBest,\nPROMISE",
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
