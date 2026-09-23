from __future__ import annotations

from enum import Enum

from promise_domain.enums import Priority
from pydantic import BaseModel, Field


class ExtractionCategory(str, Enum):
    """Every shape of input the engine must tell apart (see the product rule in
    the Commitment Detection Engine spec: commitments vs. suggestions vs.
    intentions vs. hypotheticals vs. other people's obligations vs. questions
    vs. quoted text vs. past actions)."""

    COMMITMENT = "commitment"
    SUGGESTION = "suggestion"
    HYPOTHETICAL = "hypothetical"
    OTHER_PERSON_OBLIGATION = "other_person_obligation"
    QUESTION = "question"
    QUOTED = "quoted"
    PAST_ACTION = "past_action"
    NONE = "none"


class RawCommitmentExtraction(BaseModel):
    """What a `CommitmentExtractionProvider` returns: classification plus raw spans.

    Deliberately excludes any absolute date/time. The provider (LLM or mock)
    only ever extracts the *relative* temporal expression as written
    ("tomorrow morning", "next Friday") in `temporal_expression` — resolving
    it against a reference clock is `commitment_extraction.temporal`'s job,
    never the model's, so the model can't hallucinate today's date.
    """

    is_commitment: bool
    category: ExtractionCategory
    action: str | None = None
    contact_name: str | None = None
    temporal_expression: str | None = None
    priority_hint: Priority | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None


class CommitmentExtraction(BaseModel):
    """Validated, timezone-resolved extraction output — the contract between
    `CommitmentExtractor` and the application layer. Field names mirror the
    `Commitment` / `CommitmentSource` domain models so a caller can persist
    directly without renaming anything.
    """

    is_commitment: bool
    category: ExtractionCategory
    action: str | None = None
    contact_name: str | None = None
    due_at: str | None = None
    priority: Priority = Priority.MEDIUM
    confidence: float = Field(ge=0.0, le=1.0)
    source_excerpt: str
    reasoning: str | None = None
    provider_name: str
    model_id: str | None = None
