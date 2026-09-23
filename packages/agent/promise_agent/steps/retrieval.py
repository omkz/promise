from __future__ import annotations

from typing import Any

from promise_domain.models import Commitment, Contact

from ..context import AgentRepos
from ..context_retrieval import ContextRetriever, build_commitment_query

"""Application-facing retrieval step.

All the actual retrieval logic (search-provider fan-out, dedup, ranking,
explainability) lives in `promise_agent.context_retrieval` and is independent
of FastAPI/MCP/Alexa+/DynamoDB. This function's only job is wiring: build a
query from the commitment, run it against whichever `IntegrationProvider` is
currently configured on `repos`, and hand the (structured, not string) result
to the planning step — `ContextRetriever` is constructed fresh per call so a
swapped-in integration provider (e.g. in tests) is always the one used.
"""


def retrieve_context(commitment: Commitment, contact: Contact | None, repos: AgentRepos) -> dict[str, Any]:
    query = build_commitment_query(commitment, contact, user_id=commitment.user_id)
    retriever = ContextRetriever(repos.integration)
    outcome = retriever.retrieve(query)
    return {"items": outcome.items, "status": outcome.status, "errors": outcome.errors, "request_id": outcome.request_id}
