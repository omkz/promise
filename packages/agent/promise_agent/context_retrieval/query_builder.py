from __future__ import annotations

import re

from promise_domain.models import Commitment, Contact

from .schema import ContextQuery

"""Builds a `ContextQuery` from a `Commitment` — the one piece of "derive query
terms from the commitment" logic the spec allows the retrieval engine to own.
Deliberately just tokenization, no LLM: deterministic filtering must stay
separate from LLM reasoning (the extraction engine already did any LLM work,
upstream of this)."""

_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "and", "or", "with", "on", "in", "at", "is", "it",
    "i", "i'll", "i'm", "will", "need", "have", "please", "this", "that",
}
_TOKEN_PATTERN = re.compile(r"[a-z0-9']+")


def _keywords(*texts: str) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for text in texts:
        for token in _TOKEN_PATTERN.findall(text.lower()):
            if token in _STOPWORDS or len(token) <= 1 or token in seen:
                continue
            seen.add(token)
            keywords.append(token)
    return keywords


def build_commitment_query(
    commitment: Commitment, contact: Contact | None, *, user_id: str | None = None, limit: int | None = None
) -> ContextQuery:
    return ContextQuery(
        workspace_id=commitment.workspace_id,
        user_id=user_id or commitment.user_id,
        commitment_id=commitment.id,
        text=commitment.title or commitment.action,
        contact_id=contact.id if contact else None,
        contact_name=contact.name if contact else None,
        keywords=_keywords(commitment.title, commitment.action, commitment.description),
        limit=limit if limit is not None else 5,
    )
