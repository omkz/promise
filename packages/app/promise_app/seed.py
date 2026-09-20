from __future__ import annotations

from promise_domain.models import Contact, Document, Message

from .bootstrap import AppContext, build_context

"""Seed data for the Andi/Sarah demo scenario.

This is seed data, not architecture: it populates the default dev
workspace with a contact, a proposal document, feedback, and a message so
the demo flow ("I'll send Andi the revised proposal tomorrow morning" ->
"Handle Andi") has something concrete to retrieve. Nothing in the domain,
agent, or app layers knows these names exist.
"""


def seed(ctx: AppContext | None = None) -> AppContext:
    ctx = ctx or build_context()
    workspace_id = ctx.default_workspace_id

    andi = Contact(id="con_andi", workspace_id=workspace_id, name="Andi", email="andi@example.com")
    sarah = Contact(id="con_sarah", workspace_id=workspace_id, name="Sarah", email="sarah@example.com")
    ctx.repos.contacts.save(andi)
    ctx.repos.contacts.save(sarah)

    ctx.repos.documents.save(
        Document(
            id="doc_andi_v3",
            workspace_id=workspace_id,
            name="Andi_Proposal_v3.docx",
            type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content_text=(
                "ACME Implementation Proposal\n\nPricing\nBase package: $18,000\n\n"
                "Implementation\nPhase 1 discovery\nPhase 2 build\nPhase 3 launch\n"
            ),
            metadata={"tags": ["andi", "proposal", "acme"]},
        )
    )
    ctx.repos.documents.save(
        Document(
            id="doc_sarah_feedback",
            workspace_id=workspace_id,
            name="Sarah_Feedback.txt",
            type="text/plain",
            content_text=(
                "Please update the pricing section to reflect the latest discount "
                "and add a clear implementation timeline with target dates."
            ),
            metadata={"tags": ["sarah", "feedback", "proposal"]},
        )
    )
    ctx.repos.messages.save(
        Message(
            id="msg_andi",
            workspace_id=workspace_id,
            sender="Andi",
            recipient="Kurnia",
            subject="Proposal timing",
            content="Can you send the revised proposal tomorrow?",
        )
    )
    ctx.repos.messages.save(
        Message(
            id="msg_sarah",
            workspace_id=workspace_id,
            sender="Sarah",
            recipient="Kurnia",
            subject="Feedback on Andi proposal",
            content=(
                "Please update the pricing section to reflect the latest discount "
                "and add a clear implementation timeline with target dates."
            ),
        )
    )
    return ctx


if __name__ == "__main__":
    seed()
    print("Seeded PROMISE dev workspace with the Andi/Sarah demo scenario.")
