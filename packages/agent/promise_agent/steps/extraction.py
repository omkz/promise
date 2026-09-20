from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from promise_domain.enums import CommitmentStatus, Priority
from promise_domain.models import Commitment, CommitmentSource, Contact
from promise_shared.clock import iso_now
from promise_shared.ids import new_id

from ..context import AgentRepos

_ACTION_PATTERN = re.compile(
    r"\b(?:send|email|deliver|share|submit|finish|review|call|follow up|follow-up|check|confirm)\b(.+?)"
    r"(?:\s+tomorrow|\s+today|$)",
    re.I,
)
_PERSON_PATTERN = re.compile(r"\b(?:to|send|email|share|deliver|submit)\s+([A-Z][a-zA-Z'-]+)")


def _parse_due(text: str) -> str | None:
    tz_name = os.getenv("PROMISE_TIMEZONE", "Asia/Jakarta")
    now = datetime.now(ZoneInfo(tz_name))
    lower = text.lower()
    if "tomorrow" in lower:
        hour = 9 if "morning" in lower else now.hour
        return (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()
    if "today" in lower:
        return now.replace(hour=max(now.hour, 9), minute=0, second=0, microsecond=0).isoformat()
    return None


def extract_commitment(
    text: str,
    workspace_id: str,
    user_id: str,
    repos: AgentRepos,
    *,
    source_type: str = "conversation",
    source_system: str = "web",
    source_ref: str | None = None,
) -> dict:
    """Deterministically detect a commitment in free text and persist it with provenance.

    This is intentionally rule-based rather than model-based: commitment
    detection has to be reproducible and auditable ("why do you think I
    promised this?"), and a regex match is trivially explainable. A future
    version can add an LLM-assisted extractor behind the same signature.
    """
    clean = text.strip()
    match = _ACTION_PATTERN.search(clean)
    action = match.group(0).strip() if match else clean
    confidence = 0.9 if match else 0.5

    contact: Contact | None = None
    person_match = _PERSON_PATTERN.search(clean)
    if person_match:
        name = person_match.group(1)
        existing = next(
            (c for c in repos.contacts.list(workspace_id) if c.name.lower() == name.lower()),
            None,
        )
        contact = existing or repos.contacts.save(
            Contact(id=new_id("con"), workspace_id=workspace_id, name=name, email=f"{name.lower()}@example.com")
        )

    commitment_id = new_id("com")
    source_id = new_id("src")
    commitment = Commitment(
        id=commitment_id,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        title=action,
        description=clean,
        contact_id=contact.id if contact else None,
        due_at=_parse_due(clean),
        priority=Priority.HIGH if any(k in clean.lower() for k in ["tomorrow", "today", "urgent"]) else Priority.MEDIUM,
        status=CommitmentStatus.OPEN,
        confidence=confidence,
        source_id=source_id,
    )
    source = CommitmentSource(
        id=source_id,
        workspace_id=workspace_id,
        commitment_id=commitment_id,
        source_type=source_type,
        source_system=source_system,
        source_id=source_ref or f"{source_system}:{new_id('evt')}",
        excerpt=clean,
        occurred_at=iso_now(),
        confidence=confidence,
    )
    repos.commitments.save(commitment)
    repos.sources.save(source)
    return {"commitment": commitment, "source": source, "contact": contact}
