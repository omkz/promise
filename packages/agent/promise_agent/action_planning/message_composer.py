from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from promise_domain.models import Commitment, Contact

"""Message composition, separated out from `SendMessagePlanner` so subject/body
generation is independently swappable — a future context-aware or
Bedrock-assisted composer is a new `MessageComposer` implementation, not a
change to the planner.

v1 ships one implementation, `DeterministicMessageComposer`: no LLM, always
available (the required "deterministic fallback for local development"), and
grounded only in real data — the commitment's own title, the contact's real
name, and the `changes` list `llm.revise_document` already computed. It never
invents a factual claim about what changed.
"""


@dataclass
class ComposedMessage:
    subject: str
    body: str


class MessageComposer(Protocol):
    def compose(self, *, commitment: Commitment, contact: Contact, changes: list[str]) -> ComposedMessage: ...


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
