from __future__ import annotations

import re

from promise_domain.enums import CLOSED_COMMITMENT_STATUSES
from promise_domain.models import Commitment


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


def _date_only(due_at: str | None) -> str | None:
    return due_at[:10] if due_at else None


def find_duplicate(
    existing: list[Commitment], *, user_id: str, action: str | None, contact_id: str | None, due_at: str | None
) -> Commitment | None:
    """Deterministic duplicate check: same user, same normalized action text,
    same contact, and the same due-date window (calendar day). No fuzzy or
    semantic matching — see the Commitment Detection Engine spec.
    """
    normalized_action = _normalize(action)
    if not normalized_action:
        return None
    target_day = _date_only(due_at)
    for candidate in existing:
        if candidate.user_id != user_id or candidate.status in CLOSED_COMMITMENT_STATUSES:
            continue
        if _normalize(candidate.action) != normalized_action:
            continue
        if candidate.contact_id != contact_id:
            continue
        if _date_only(candidate.due_at) != target_day:
            continue
        return candidate
    return None
