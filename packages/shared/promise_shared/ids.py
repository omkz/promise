from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """Generate a short, prefixed, sortable-enough identifier.

    Prefixes make ids self-describing in logs, audit trails, and DynamoDB
    keys (e.g. "com_", "act_", "run_") without needing a lookup.
    """
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
