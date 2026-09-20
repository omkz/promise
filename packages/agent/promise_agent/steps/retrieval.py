from __future__ import annotations

from promise_domain.models import Commitment, Contact

from ..context import AgentRepos


def retrieve_context(commitment: Commitment, contact: Contact | None, repos: AgentRepos) -> dict:
    """Gather documents/messages relevant to a commitment via the integration provider.

    Goes through `repos.integration` (not the document/message repositories
    directly) so retrieval works the same way regardless of whether the
    backing provider is the local demo store or a real Gmail/Drive account.
    """
    workspace_id = commitment.workspace_id
    search_term = contact.name if contact else commitment.title
    documents = repos.integration.search_files(workspace_id, search_term)
    if not documents:
        documents = repos.integration.search_files(workspace_id, "proposal")

    messages = repos.integration.search_messages(workspace_id, "feedback")
    if not messages:
        messages = repos.integration.search_messages(workspace_id, search_term)

    return {"documents": documents, "messages": messages}
