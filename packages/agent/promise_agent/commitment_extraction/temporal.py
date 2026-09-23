from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

"""Deterministic relative-date resolution for commitment extraction.

The LLM (or mock) provider never computes an absolute date — it only ever
extracts the relative phrase as written (see `RawCommitmentExtraction.
temporal_expression`). This module resolves that phrase against a reference
`datetime` supplied explicitly by the caller, so results are reproducible and
independent of the extraction provider.

When a phrase names a day but not a time of day ("tomorrow", "Friday", "next
week"), the default time of day is documented and configurable rather than
hallucinated: it defaults to the "morning" daypart below, overridable via
`PROMISE_DEFAULT_MORNING_HOUR` (and the sibling `_AFTERNOON_`/`_EVENING_`/
`_NIGHT_` variables for their respective dayparts).
"""

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_DAYPART_ENV = {
    "morning": "PROMISE_DEFAULT_MORNING_HOUR",
    "afternoon": "PROMISE_DEFAULT_AFTERNOON_HOUR",
    "evening": "PROMISE_DEFAULT_EVENING_HOUR",
    "night": "PROMISE_DEFAULT_NIGHT_HOUR",
}
_DAYPART_DEFAULTS = {"morning": 9, "afternoon": 14, "evening": 18, "night": 20}

_TEMPORAL_PATTERN = re.compile(
    r"\btomorrow(?:\s+(?:morning|afternoon|evening|night))?\b"
    r"|\btonight\b"
    r"|\bthis\s+(?:morning|afternoon|evening)\b"
    r"|\btoday\b"
    r"|\bnext\s+week\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:\s+(?:morning|afternoon|evening|night))?\b",
    re.IGNORECASE,
)


def _daypart_hour(daypart: str | None) -> int:
    """The documented default: an unqualified day resolves to the morning hour."""
    key = daypart or "morning"
    return int(os.getenv(_DAYPART_ENV[key], str(_DAYPART_DEFAULTS[key])))


def extract_temporal_phrase(text: str) -> str | None:
    """Return the first relative-date span in `text` verbatim (e.g. "tomorrow
    morning", "next week"), or None. Pure pattern matching over a fixed
    vocabulary — no date math happens here."""
    match = _TEMPORAL_PATTERN.search(text)
    return match.group(0) if match else None


def resolve_due_at(phrase: str | None, *, now: datetime) -> str | None:
    """Resolve a relative temporal phrase into an absolute, timezone-aware ISO
    datetime string. `now` must already carry the target tzinfo — this
    function never reads the system clock or a timezone itself, so it is
    fully reproducible in tests."""
    if not phrase:
        return None
    lower = phrase.lower().strip()

    daypart_match = re.search(r"(morning|afternoon|evening|night)$", lower)
    daypart = daypart_match.group(1) if daypart_match else None

    if lower.startswith("tomorrow"):
        target = now + timedelta(days=1)
        hour = _daypart_hour(daypart)
    elif lower == "tonight":
        target = now
        hour = _daypart_hour("night")
    elif lower.startswith("this "):
        target = now
        hour = _daypart_hour(daypart)
    elif lower == "today":
        target = now
        hour = max(now.hour, _daypart_hour("morning"))
    elif lower == "next week":
        target = now + timedelta(days=7)
        hour = _daypart_hour(daypart)
    else:
        weekday_match = re.match(r"([a-z]+)", lower)
        weekday_name = weekday_match.group(1) if weekday_match else None
        if weekday_name not in _WEEKDAYS:
            return None
        target_idx = _WEEKDAYS.index(weekday_name)
        delta = (target_idx - now.weekday()) % 7
        if delta == 0:
            delta = 7  # naming a weekday means the next one, not "later today"
        target = now + timedelta(days=delta)
        hour = _daypart_hour(daypart)

    return target.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()
