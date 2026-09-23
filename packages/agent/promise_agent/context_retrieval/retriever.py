from __future__ import annotations

import logging
import time

from promise_integrations.base import IntegrationProvider
from promise_shared.errors import ContextRetrievalProviderError
from promise_shared.ids import new_id

from .providers import ContextSearchProvider, DocumentSearchProvider, MessageSearchProvider, RawContextHit
from .ranking import DeterministicLexicalRanker, RankingStrategy
from .schema import ContextQuery, RetrievalOutcome, RetrievalStatus

logger = logging.getLogger("promise.context_retrieval")


def _in_time_window(hit: RawContextHit, query: ContextQuery) -> bool:
    window = query.time_window
    if window is None or hit.timestamp is None:
        return True
    # All internal timestamps come from `promise_shared.clock.iso_now()`, which always
    # produces the same offset format, so a plain string comparison sorts chronologically
    # without needing to parse timezones here.
    if window.start and hit.timestamp < window.start:
        return False
    if window.end and hit.timestamp > window.end:
        return False
    return True


class ContextRetriever:
    """Turns a `ContextQuery` into ranked, explainable `ContextItem`s.

    Independent of FastAPI, MCP, Alexa+, and DynamoDB — it only depends on the
    existing `IntegrationProvider` abstraction (documents/messages today; a
    future Gmail/Drive/Slack/Notion/Microsoft 365 provider needs no changes
    here, only a new `IntegrationProvider` implementation) and a
    `RankingStrategy` (deterministic lexical today; a semantic/vector ranker
    later is a drop-in replacement — see `ranking.py`).

    Deliberately stateless/cheap to construct: callers build one per request
    against whichever `IntegrationProvider` is currently configured, so
    swapping the active provider (e.g. in tests) is always picked up — there
    is no long-lived binding to a stale provider instance.
    """

    def __init__(
        self, integration: IntegrationProvider, *, providers: list[ContextSearchProvider] | None = None,
        ranker: RankingStrategy | None = None,
    ) -> None:
        self._providers = providers or [DocumentSearchProvider(integration), MessageSearchProvider(integration)]
        self._ranker = ranker or DeterministicLexicalRanker()

    def retrieve(self, query: ContextQuery) -> RetrievalOutcome:
        request_id = new_id("ret")
        started = time.monotonic()

        active_providers = [p for p in self._providers if _provider_relevant(p, query)]

        all_hits: list[RawContextHit] = []
        errors: list[dict] = []
        for provider in active_providers:
            try:
                all_hits.extend(provider.fetch_candidates(query))
            except ContextRetrievalProviderError as exc:
                errors.append({"provider": exc.provider_name, "error": str(exc), "retryable": exc.retryable})

        # Defense in depth: a provider must never hand back another workspace's data,
        # even though each provider already checks this itself (see providers.py).
        all_hits = [h for h in all_hits if h.workspace_id == query.workspace_id]

        deduped = _dedupe(all_hits)
        deduped = [h for h in deduped if _in_time_window(h, query)]

        items = self._ranker.rank(query, deduped)
        limited = items[: max(query.limit, 0)]

        status = RetrievalStatus.PARTIAL if errors else RetrievalStatus.COMPLETE
        duration_ms = (time.monotonic() - started) * 1000
        logger.info(
            "context_retrieval request_id=%s workspace=%s commitment=%s providers=%s result_count=%d "
            "duration_ms=%.1f status=%s errors=%d ranking_strategy=%s",
            request_id, query.workspace_id, query.commitment_id, [p.name for p in active_providers],
            len(limited), duration_ms, status.value, len(errors), self._ranker.name,
        )

        return RetrievalOutcome(
            request_id=request_id, items=limited, status=status, errors=errors, ranking_strategy=self._ranker.name
        )


def _provider_relevant(provider: ContextSearchProvider, query: ContextQuery) -> bool:
    return query.types is None or provider.produces_type in query.types


def _dedupe(hits: list[RawContextHit]) -> list[RawContextHit]:
    """Same artifact matched by multiple search terms -> one hit, with every
    matching term preserved so the ranker can explain every signal that fired."""
    merged: dict[tuple, RawContextHit] = {}
    for hit in hits:
        existing = merged.get(hit.dedupe_key)
        if existing is None:
            merged[hit.dedupe_key] = hit
        else:
            existing.matched_terms |= hit.matched_terms
    return list(merged.values())
