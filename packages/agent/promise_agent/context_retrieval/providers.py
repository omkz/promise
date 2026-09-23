from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from promise_integrations.base import IntegrationProvider
from promise_shared.errors import ContextRetrievalProviderError

from .schema import ContextItemType, ContextQuery

"""Search-provider abstraction for context retrieval.

`ContextSearchProvider` is the seam that makes "documents", "messages", and
any future search surface (PostgreSQL full-text search, vector/semantic
search, Gmail, Google Drive, Slack, Notion, Microsoft 365, ...) pluggable
without `ContextRetriever` or anything above it changing. v1 ships two
implementations, both adapting the *existing* `IntegrationProvider`
abstraction (`packages/integrations`) rather than inventing a parallel one —
that's the same seam `packages/app/promise_app/tools.py` already uses for
search/get, so a future real Gmail/Drive provider needs no new plumbing here,
only a new `IntegrationProvider` implementation.
"""


@dataclass
class RawContextHit:
    """An unscored candidate, before ranking. Internal to this package — never
    returned to a caller (see `ContextItem` for the public, scored shape)."""

    type: ContextItemType
    workspace_id: str
    source_id: str
    title: str
    content_text: str
    source_system: str
    metadata: dict[str, Any]
    uri: str | None = None
    sender: str | None = None
    recipient: str | None = None
    timestamp: str | None = None
    """`created_at`/`updated_at` for a document, `occurred_at` for a message —
    normalized to one field name so the ranker's recency signal doesn't need
    to know which type it's looking at."""
    matched_terms: set[str] = field(default_factory=set)

    @property
    def dedupe_key(self) -> tuple[ContextItemType, str]:
        return (self.type, self.source_id)


class ContextSearchProvider(Protocol):
    """One search surface a `ContextRetriever` fans out to."""

    name: str
    produces_type: ContextItemType
    """Lets `ContextRetriever` skip a provider outright when `ContextQuery.types`
    excludes it, without needing to know provider names."""

    def fetch_candidates(self, query: ContextQuery) -> list[RawContextHit]:
        """Raises `ContextRetrievalProviderError` on failure — never returns a
        fabricated result. Never returns candidates outside `query.workspace_id`."""
        ...


def _search_terms(query: ContextQuery) -> list[str]:
    """Terms to search the underlying provider with — not the ranking signal
    itself (that's `ranking.py`'s job). Deliberately queries per-term rather
    than relying on any one provider's "empty query lists everything" quirk,
    so this works the same way against a real remote search API later."""
    terms: list[str] = []
    if query.contact_name:
        terms.append(query.contact_name)
    terms.extend(query.keywords)
    if query.text:
        terms.append(query.text)

    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        key = term.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(term.strip())
    return unique


class DocumentSearchProvider:
    """Adapts `IntegrationProvider.search_files` into `ContextSearchProvider`."""

    name = "documents"
    produces_type = ContextItemType.DOCUMENT

    def __init__(self, integration: IntegrationProvider) -> None:
        self._integration = integration

    def fetch_candidates(self, query: ContextQuery) -> list[RawContextHit]:
        by_id: dict[str, RawContextHit] = {}
        try:
            for term in _search_terms(query):
                for doc in self._integration.search_files(query.workspace_id, term):
                    if doc.get("workspace_id") != query.workspace_id:
                        continue  # defense in depth: never trust a provider's own scoping
                    hit = by_id.get(doc["id"])
                    if hit is None:
                        hit = RawContextHit(
                            type=ContextItemType.DOCUMENT,
                            workspace_id=doc["workspace_id"],
                            source_id=doc["id"],
                            title=doc.get("name", doc["id"]),
                            content_text=doc.get("content_text", ""),
                            source_system=self._integration.provider_name,
                            metadata={"tags": doc.get("metadata", {}).get("tags", [])},
                            timestamp=doc.get("updated_at") or doc.get("created_at"),
                        )
                        by_id[doc["id"]] = hit
                    hit.matched_terms.add(term)
        except ContextRetrievalProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - convert to the controlled provider error
            raise ContextRetrievalProviderError(self.name, str(exc)) from exc
        return list(by_id.values())


class MessageSearchProvider:
    """Adapts `IntegrationProvider.search_messages` into `ContextSearchProvider`."""

    name = "messages"
    produces_type = ContextItemType.MESSAGE

    def __init__(self, integration: IntegrationProvider) -> None:
        self._integration = integration

    def fetch_candidates(self, query: ContextQuery) -> list[RawContextHit]:
        by_id: dict[str, RawContextHit] = {}
        try:
            for term in _search_terms(query):
                for msg in self._integration.search_messages(query.workspace_id, term):
                    if msg.get("workspace_id") != query.workspace_id:
                        continue
                    hit = by_id.get(msg["id"])
                    if hit is None:
                        hit = RawContextHit(
                            type=ContextItemType.MESSAGE,
                            workspace_id=msg["workspace_id"],
                            source_id=msg["id"],
                            title=msg.get("subject") or f"Message from {msg.get('sender', 'unknown')}",
                            content_text=msg.get("content", ""),
                            source_system=self._integration.provider_name,
                            metadata={"sender": msg.get("sender"), "recipient": msg.get("recipient")},
                            sender=msg.get("sender"),
                            recipient=msg.get("recipient"),
                            timestamp=msg.get("occurred_at"),
                        )
                        by_id[msg["id"]] = hit
                    hit.matched_terms.add(term)
        except ContextRetrievalProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ContextRetrievalProviderError(self.name, str(exc)) from exc
        return list(by_id.values())
