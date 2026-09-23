from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Protocol

from .providers import RawContextHit
from .schema import ContextItem, ContextItemType, ContextQuery, ContextSource

"""Deterministic, explainable ranking for context retrieval.

`RankingStrategy` is the seam a future semantic/vector ranker plugs into
without `ContextRetriever` changing — v1 ships exactly one implementation,
`DeterministicLexicalRanker`, which is pure keyword/metadata matching. It
never claims semantic relevance; every score is traceable to one of a fixed
set of signals, each surfaced in `ContextItem.match_reasons`.
"""

_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "and", "or", "with", "on", "in", "at", "is", "it",
    "i", "i'll", "i'm", "will", "need", "have", "please", "this", "that", "send", "review",
}

_TOKEN_PATTERN = re.compile(r"[a-z0-9']+")

# Weights sum to 1.0, so the combined score is naturally in [0, 1] without extra clamping.
_WEIGHT_CONTACT = 0.40
_WEIGHT_TITLE = 0.25
_WEIGHT_PHRASE = 0.25
_WEIGHT_RECENCY = 0.10

# Deterministic tie-break only (see RANKING signal 5, "document/message type relevance"):
# used exclusively to order otherwise-equal-scoring items, never added into the score itself.
_TYPE_PRIORITY = {ContextItemType.DOCUMENT: 0, ContextItemType.MESSAGE: 1, ContextItemType.NOTE: 2}

_RECENCY_HALF_LIFE_DAYS = 30.0


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_PATTERN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1}


def _query_tokens(query: ContextQuery) -> set[str]:
    tokens = _tokenize(query.text)
    for kw in query.keywords:
        tokens |= _tokenize(kw)
    return tokens


def _contact_signal(query: ContextQuery, hit: RawContextHit) -> tuple[float, str | None]:
    if not query.contact_name:
        return 0.0, None
    name = query.contact_name.strip().lower()
    if not name:
        return 0.0, None
    fields = [hit.sender, hit.recipient, hit.title]
    fields += [str(t) for t in hit.metadata.get("tags", [])]
    if any(f and f.strip().lower() == name for f in fields):
        return 1.0, f"Matched contact: {query.contact_name}"
    if any(f and name in f.lower() for f in fields):
        return 0.75, f"Matched contact: {query.contact_name}"
    if name in hit.content_text.lower():
        return 0.4, f"Matched contact: {query.contact_name}"
    return 0.0, None


def _title_signal(tokens: set[str], hit: RawContextHit) -> tuple[float, str | None]:
    if not tokens:
        return 0.0, None
    title_tokens = _tokenize(hit.title)
    if not title_tokens:
        return 0.0, None
    overlap = tokens & title_tokens
    if not overlap:
        return 0.0, None
    score = len(overlap) / len(tokens)
    return min(score, 1.0), f"Matched title: {hit.title}"


def _phrase_signal(query: ContextQuery, tokens: set[str], hit: RawContextHit) -> tuple[float, str | None]:
    content_lower = hit.content_text.lower()
    phrase = query.text.strip().lower()
    if phrase and len(phrase) > 3 and phrase in content_lower:
        return 1.0, f'Matched phrase: "{query.text.strip()}"'
    if not tokens:
        return 0.0, None
    content_tokens = _tokenize(hit.content_text)
    overlap = tokens & content_tokens
    if not overlap:
        return 0.0, None
    score = len(overlap) / len(tokens)
    return min(score, 1.0), f"Matched keyword(s): {', '.join(sorted(overlap))}"


def _recency_signal(hit: RawContextHit, *, now: datetime) -> tuple[float, str | None]:
    if not hit.timestamp:
        return 0.0, None
    try:
        ts = datetime.fromisoformat(hit.timestamp)
    except ValueError:
        return 0.0, None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = max((now - ts).total_seconds() / 86400.0, 0.0)
    # Exponential decay: full weight when brand new, half weight at the half-life, and so on.
    score = 0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS)
    reason = "Recent activity" if score >= 0.5 else None
    return score, reason


class RankingStrategy(Protocol):
    name: str

    def rank(self, query: ContextQuery, hits: list[RawContextHit]) -> list[ContextItem]: ...


class DeterministicLexicalRanker:
    """v1's only ranker: contact match, title match, phrase/token match, and
    recency — each independently explainable, combined with fixed weights.
    Ties are broken by type priority (document > message > note), then by
    recency, then by id, so ordering is fully deterministic."""

    name = "deterministic_lexical_v1"

    def rank(self, query: ContextQuery, hits: list[RawContextHit], *, now: datetime | None = None) -> list[ContextItem]:
        now = now or datetime.now(timezone.utc)
        tokens = _query_tokens(query)
        items: list[ContextItem] = []

        for hit in hits:
            contact_score, contact_reason = _contact_signal(query, hit)
            title_score, title_reason = _title_signal(tokens, hit)
            phrase_score, phrase_reason = _phrase_signal(query, tokens, hit)
            recency_score, recency_reason = _recency_signal(hit, now=now)

            score = (
                _WEIGHT_CONTACT * contact_score
                + _WEIGHT_TITLE * title_score
                + _WEIGHT_PHRASE * phrase_score
                + _WEIGHT_RECENCY * recency_score
            )
            reasons = [r for r in (contact_reason, title_reason, phrase_reason, recency_reason) if r]
            if not reasons:
                reasons = ["Weak or no signal match"]

            items.append(
                ContextItem(
                    id=hit.source_id,
                    type=hit.type,
                    title=hit.title,
                    snippet=_snippet(hit.content_text),
                    score=round(min(max(score, 0.0), 1.0), 4),
                    match_reasons=reasons,
                    source=ContextSource(system=hit.source_system, source_id=hit.source_id, uri=hit.uri),
                    metadata={**hit.metadata, "timestamp": hit.timestamp},
                )
            )

        items.sort(key=lambda item: (-item.score, _TYPE_PRIORITY.get(item.type, 99), item.id))
        return items


def _snippet(text: str, *, max_chars: int = 220) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"
