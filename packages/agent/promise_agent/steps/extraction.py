from __future__ import annotations

from datetime import datetime
from typing import Any

from promise_domain.enums import CommitmentStatus
from promise_domain.models import Commitment, CommitmentSource, Contact
from promise_shared.clock import iso_now
from promise_shared.ids import new_id

from ..commitment_extraction import CommitmentExtraction, CommitmentExtractor, ExtractionCategory, config, find_duplicate
from ..context import AgentRepos

"""Application-facing commitment extraction step.

This is the only place that persists anything: `CommitmentExtractor` (see
`promise_agent.commitment_extraction`) is a pure text -> `CommitmentExtraction`
transformation with no knowledge of repositories, DynamoDB, FastAPI, or MCP.
This module owns everything downstream of that: the auto-capture confidence
gate, contact lookup/creation, duplicate prevention, and writing the
`Commitment`/`CommitmentSource` pair with full provenance.
"""

_NOT_A_COMMITMENT_REASONS = {
    ExtractionCategory.SUGGESTION: "The statement reads as a suggestion, not a commitment you made.",
    ExtractionCategory.HYPOTHETICAL: "The statement reads as hypothetical, not a commitment you made.",
    ExtractionCategory.OTHER_PERSON_OBLIGATION: "This describes someone else's obligation, not something you committed to.",
    ExtractionCategory.QUOTED: "This reports what someone else said, not your own commitment.",
    ExtractionCategory.QUESTION: "This is a question or request, not a personal commitment.",
    ExtractionCategory.PAST_ACTION: "This describes something already done, not an open commitment.",
    ExtractionCategory.NONE: "No personal commitment was detected in that statement.",
}


def _not_detected(extraction: CommitmentExtraction) -> dict[str, Any]:
    return {
        "detected": False,
        "persisted": False,
        "needs_confirmation": False,
        "duplicate": False,
        "commitment": None,
        "source": None,
        "contact": None,
        "extraction": extraction,
        "reason": _NOT_A_COMMITMENT_REASONS.get(extraction.category, _NOT_A_COMMITMENT_REASONS[ExtractionCategory.NONE]),
    }


def _needs_confirmation(extraction: CommitmentExtraction) -> dict[str, Any]:
    return {
        "detected": True,
        "persisted": False,
        "needs_confirmation": True,
        "duplicate": False,
        "commitment": None,
        "source": None,
        "contact": None,
        "extraction": extraction,
        "message": f'I\'m thinking this may be a commitment: "{extraction.action or extraction.source_excerpt}". Capture it?',
    }


def _resolve_contact(workspace_id: str, contact_name: str | None, repos: AgentRepos) -> Contact | None:
    if not contact_name:
        return None
    existing = next((c for c in repos.contacts.list(workspace_id) if c.name.lower() == contact_name.lower()), None)
    if existing:
        return existing
    # A contact created from a bare detected name has no verified email — PROMISE never
    # invents one (e.g. "andi@example.com" from the name "Andi"). Anything that needs to
    # actually send to this contact must check `contact.email` first; see
    # `promise_agent.steps.planning` and `promise_app.tools.create_draft`.
    return repos.contacts.save(Contact(id=new_id("con"), workspace_id=workspace_id, name=contact_name, email=None))


def _validate_occurred_at(occurred_at: str | None) -> str | None:
    if occurred_at is None:
        return None
    try:
        datetime.fromisoformat(occurred_at)
    except ValueError as exc:
        raise ValueError(f"invalid occurred_at timestamp: {occurred_at!r}") from exc
    return occurred_at


def extract_commitment(
    text: str,
    workspace_id: str,
    user_id: str,
    repos: AgentRepos,
    *,
    source_type: str = "conversation",
    source_system: str = "web",
    source_ref: str | None = None,
    occurred_at: str | None = None,
    confirm: bool = False,
    extractor: CommitmentExtractor | None = None,
    now: datetime | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Detect a commitment in free text and, when it clears the auto-capture
    bar, persist it with full provenance.

    `occurred_at`, when the caller has it (e.g. an Alexa transcript timestamp,
    or an email's original send time), is the source/utterance's own
    timestamp — kept distinct from `CommitmentSource.created_at`, which is
    always when PROMISE actually processed/persisted it. When the caller
    doesn't have one, `occurred_at` falls back to processing time too, since
    that's the best available estimate — but the two remain independent
    fields, never silently conflated.

    Returns a dict always carrying `detected`/`persisted`/`extraction`; when
    `is_commitment` is false nothing is written (see `_not_detected`), and
    when it's true but under-confident nothing is written either until the
    caller re-invokes with `confirm=True` (see `_needs_confirmation`).
    """
    occurred_at = _validate_occurred_at(occurred_at)
    extractor = extractor or CommitmentExtractor()
    extraction = extractor.extract(text, now=now, timezone=timezone)

    if not extraction.is_commitment:
        return _not_detected(extraction)

    if extraction.confidence < config.auto_capture_threshold() and not confirm:
        return _needs_confirmation(extraction)

    contact = _resolve_contact(workspace_id, extraction.contact_name, repos)

    existing = repos.commitments.list(workspace_id)
    duplicate = find_duplicate(
        existing, user_id=user_id, action=extraction.action, contact_id=contact.id if contact else None, due_at=extraction.due_at
    )
    if duplicate is not None:
        source = repos.sources.get(workspace_id, duplicate.source_id) if duplicate.source_id else None
        return {
            "detected": True,
            "persisted": True,
            "needs_confirmation": False,
            "duplicate": True,
            "commitment": duplicate,
            "source": source,
            "contact": contact,
            "extraction": extraction,
        }

    action_text = extraction.action or extraction.source_excerpt
    commitment_id = new_id("com")
    source_id = new_id("src")
    commitment = Commitment(
        id=commitment_id,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action_text,
        title=action_text,
        description=extraction.source_excerpt,
        contact_id=contact.id if contact else None,
        due_at=extraction.due_at,
        priority=extraction.priority,
        status=CommitmentStatus.OPEN,
        confidence=extraction.confidence,
        source_id=source_id,
    )
    source = CommitmentSource(
        id=source_id,
        workspace_id=workspace_id,
        commitment_id=commitment_id,
        source_type=source_type,
        source_system=source_system,
        source_id=source_ref or f"{source_system}:{new_id('evt')}",
        excerpt=extraction.source_excerpt,
        occurred_at=occurred_at or iso_now(),
        confidence=extraction.confidence,
        # created_at is left to its own default_factory (promise_shared.clock.iso_now) so it
        # always reflects processing time — it is never accepted from a caller.
    )
    repos.commitments.save(commitment)
    repos.sources.save(source)
    return {
        "detected": True,
        "persisted": True,
        "needs_confirmation": False,
        "duplicate": False,
        "commitment": commitment,
        "source": source,
        "contact": contact,
        "extraction": extraction,
    }
