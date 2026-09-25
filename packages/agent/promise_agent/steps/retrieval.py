from __future__ import annotations

from typing import Any

from promise_domain.models import Commitment, Contact

from ..context import AgentRepos
from ..context_retrieval import ContextRetriever, build_commitment_query
from ..context_retrieval.providers import DocumentSearchProvider, MessageSearchProvider

"""Application-facing retrieval step.

All the actual retrieval logic (search-provider fan-out, dedup, ranking,
explainability) lives in `promise_agent.context_retrieval` and is independent
of FastAPI/MCP/Alexa+/DynamoDB. This function's only job is wiring: build a
query from the commitment, run it against whichever `IntegrationProvider`(s)
are relevant, and hand the (structured, not string) result to the planning
step — `ContextRetriever` is constructed fresh per call so a swapped-in
integration provider (e.g. in tests) is always the one used.

Gmail participates here as just another `ContextSearchProvider` instance
alongside the default (local/demo) one — never a special code path inside
`ContextRetriever` itself (see `context_retrieval/providers.py`). When the
commitment's own owner (`commitment.user_id`) has no connected Gmail account,
`resolve_for_user` returns `None` and retrieval runs exactly as it did before
Gmail existed.
"""


def retrieve_context(commitment: Commitment, contact: Contact | None, repos: AgentRepos) -> dict[str, Any]:
    query = build_commitment_query(commitment, contact, user_id=commitment.user_id)

    providers = [DocumentSearchProvider(repos.integration), MessageSearchProvider(repos.integration)]
    gmail = repos.integration_registry.resolve_for_user(commitment.workspace_id, commitment.user_id, provider_name="gmail")
    if gmail is not None:
        providers.append(MessageSearchProvider(gmail))

    retriever = ContextRetriever(repos.integration, providers=providers)
    outcome = retriever.retrieve(query)
    return {"items": outcome.items, "status": outcome.status, "errors": outcome.errors, "request_id": outcome.request_id}
