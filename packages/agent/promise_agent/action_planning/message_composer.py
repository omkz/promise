from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from promise_domain.models import Commitment, Contact

"""Message composition, separated out from the planners so subject/body
generation is independently swappable — a future context-aware or
Bedrock-assisted composer is a new `MessageComposer` implementation, not a
change to any planner.

v1 ships one implementation, `DeterministicMessageComposer`: no LLM, always
available (the required "deterministic fallback for local development"), and
grounded only in real data — the commitment's own title, the contact's real
name, and (for a revision) the `changes` list `llm.revise_document` already
computed. It never invents a factual claim about what changed, and
`compose_existing_document` never claims a revision happened at all.
"""


@dataclass
class ComposedMessage:
    subject: str
    body: str


class MessageComposer(Protocol):
    def compose(self, *, commitment: Commitment, contact: Contact, changes: list[str]) -> ComposedMessage:
        """For `SendRevisedDocumentPlanner`: `changes` is the real, already-computed
        list of what was revised — never fabricated here."""
        ...

    def compose_existing_document(self, *, commitment: Commitment, contact: Contact, document_name: str) -> ComposedMessage:
        """For `SendExistingDocumentPlanner`: no revision happened, so the message
        never claims one — no "summary of changes" section at all."""
        ...


class DeterministicMessageComposer:
    """No network, no AWS credentials — pure string templating over real data."""

    def compose(self, *, commitment: Commitment, contact: Contact, changes: list[str]) -> ComposedMessage:
        subject = commitment.title or "Update"
        change_lines = "\n".join(f"- {c}" for c in changes) if changes else "- Applied the latest requested updates"
        body = (
            f"Hi {contact.name},\n\n"
            f'Please find attached the update for "{subject}".\n\n'
            f"Summary of changes:\n{change_lines}\n\n"
            "Best,\nPROMISE"
        )
        return ComposedMessage(subject=subject, body=body)

    def compose_existing_document(self, *, commitment: Commitment, contact: Contact, document_name: str) -> ComposedMessage:
        subject = commitment.title or document_name
        body = (
            f"Hi {contact.name},\n\n"
            f'Please find attached "{document_name}" for "{subject}".\n\n'
            "Best,\nPROMISE"
        )
        return ComposedMessage(subject=subject, body=body)
