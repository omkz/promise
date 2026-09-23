from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ContextItemType(str, Enum):
    DOCUMENT = "document"
    MESSAGE = "message"
    NOTE = "note"  # reserved for a future integration (e.g. Notion) — not produced by v1 providers


class ContextSource(BaseModel):
    """Provenance for a `ContextItem` — where it came from and how to load it again."""

    system: str
    """e.g. "local", "gmail", "google_drive" — matches `IntegrationProvider.provider_name`."""
    source_id: str
    """The underlying document/message id — pass this to `get_file`/`get_message` for full content."""
    uri: str | None = None


class ContextItem(BaseModel):
    """A single ranked, explainable piece of context relevant to a commitment.

    Deliberately small: `snippet` is a short excerpt, never a full document body
    (see DOCUMENT CONTENT in the spec) — callers fetch full content via
    `get_file`/`get_message` using `source.source_id` once an item is selected.
    """

    id: str
    type: ContextItemType
    title: str
    snippet: str
    score: float = Field(ge=0.0, le=1.0)
    match_reasons: list[str] = Field(default_factory=list)
    source: ContextSource
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimeWindow(BaseModel):
    start: str | None = None
    end: str | None = None


class ContextQuery(BaseModel):
    """Enough context to retrieve relevant artifacts for a commitment.

    Deliberately a plain data object with no behavior: query *building* (from a
    Commitment) lives in `query_builder.py`, query *execution* lives in
    `ContextRetriever` — this type is just the contract between them, and the one
    a caller can construct directly for ad-hoc retrieval.
    """

    workspace_id: str
    user_id: str
    commitment_id: str | None = None
    text: str = ""
    contact_id: str | None = None
    contact_name: str | None = None
    keywords: list[str] = Field(default_factory=list)
    time_window: TimeWindow | None = None
    types: list[ContextItemType] | None = None
    """None means "no type filter" — every provider's items are eligible."""
    limit: int = 5


class RetrievalStatus(str, Enum):
    COMPLETE = "complete"
    """Every configured provider was queried successfully."""
    PARTIAL = "partial"
    """At least one provider failed; `errors` on the outcome says which and why.
    Results from the providers that succeeded are still returned — this is never
    silently treated the same as COMPLETE."""


class RetrievalOutcome(BaseModel):
    request_id: str
    items: list[ContextItem]
    status: RetrievalStatus
    errors: list[dict[str, Any]] = Field(default_factory=list)
    ranking_strategy: str
