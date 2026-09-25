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

An explicit clock time ("Friday at 2", "Friday at 2:30pm", "tomorrow at 14:00")
is also recognized and takes priority over the daypart default -- needed for
Calendar event creation, where an approximate "morning"/"afternoon" isn't
precise enough (see `promise_agent.action_planning.create_calendar_event_planner`).
"at 2" with no am/pm marker is a genuinely ambiguous phrase; the deterministic,
documented resolution here is the same kind of assumption the daypart defaults
already make: an hour of 1-6 with no meridiem means PM (a bare "at 2" almost
always means 2 PM in scheduling contexts, mirroring `PROMISE_DEFAULT_AFTERNOON_HOUR`'s
own use of 14:00), 7-12 means AM/noon as written. This is never an LLM guess —
it's fixed, reproducible logic, and any calendar action built from it must show
the resolved time explicitly (see that planner) so the assumption is never
silently hidden from the user.
"""

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_DAYPART_ENV = {
    "morning": "PROMISE_DEFAULT_MORNING_HOUR",
    "afternoon": "PROMISE_DEFAULT_AFTERNOON_HOUR",
    "evening": "PROMISE_DEFAULT_EVENING_HOUR",
    "night": "PROMISE_DEFAULT_NIGHT_HOUR",
}
_DAYPART_DEFAULTS = {"morning": 9, "afternoon": 14, "evening": 18, "night": 20}

_CLOCK_TIME_SUFFIX = r"(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?)?"

_TEMPORAL_PATTERN = re.compile(
    r"\b(?:"
    r"tomorrow(?:\s+(?:morning|afternoon|evening|night))?"
    r"|tonight"
    r"|this\s+(?:morning|afternoon|evening)"
    r"|today"
    r"|next\s+week"
    r"|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+(?:morning|afternoon|evening|night))?"
    r")\b" + _CLOCK_TIME_SUFFIX,
    re.IGNORECASE,
)

_CLOCK_TIME_PATTERN = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?m?\.?)?\b", re.IGNORECASE)


def _daypart_hour(daypart: str | None) -> int:
    """The documented default: an unqualified day resolves to the morning hour."""
    key = daypart or "morning"
    return int(os.getenv(_DAYPART_ENV[key], str(_DAYPART_DEFAULTS[key])))


def _explicit_clock_time(phrase: str) -> tuple[int, int] | None:
    """Parses a trailing `"at H(:MM)? (am|pm)?"` clock time out of `phrase`, or
    `None` if the phrase names no explicit time at all (just a day/daypart)."""
    match = _CLOCK_TIME_PATTERN.search(phrase)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "p" and hour != 12:
        hour += 12
    elif meridiem == "a" and hour == 12:
        hour = 0
    elif not meridiem and 1 <= hour <= 6:
        # see module docstring: a bare "at 2" with no am/pm is resolved to PM.
        hour += 12
    return hour, minute


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

    resolved = target.replace(hour=hour, minute=0, second=0, microsecond=0)
    explicit_clock_time = _explicit_clock_time(lower)
    if explicit_clock_time is not None:
        resolved = resolved.replace(hour=explicit_clock_time[0], minute=explicit_clock_time[1])
    return resolved.isoformat()
