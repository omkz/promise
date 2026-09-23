from __future__ import annotations

from promise_domain.models import Commitment

"""Shared, generic signal detection used by multiple planners to decide what
kind of "send" a commitment is asking for. Deliberately just word/verb
matching over the commitment's own text — no Andi/Sarah/filename anywhere,
and no LLM (see `selection.py`).
"""

SEND_TYPE_VERBS = ("send", "email", "share", "deliver", "submit", "forward")

# Words that indicate the commitment wants a *revised* artifact, not the document
# as it already exists. Checked against the commitment's own action/description —
# never against any specific document or contact name.
REVISION_SIGNAL_WORDS = (
    "revise", "revised", "revision",
    "update", "updated", "updating",
    "incorporat",  # matches "incorporate"/"incorporating" (feedback)
    "latest version", "new version",
    "with feedback", "with the changes", "with changes",
)


def has_send_verb(action: str | None) -> bool:
    action = (action or "").strip().lower()
    return any(action == verb or action.startswith(f"{verb} ") for verb in SEND_TYPE_VERBS)


def has_revision_signal(commitment: Commitment) -> bool:
    haystack = f"{commitment.action or ''} {commitment.description or ''}".lower()
    return any(word in haystack for word in REVISION_SIGNAL_WORDS)
