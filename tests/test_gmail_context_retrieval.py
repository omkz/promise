from __future__ import annotations

from promise_app import tools
from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount
from promise_integrations.gmail.mock_provider import MockGmailIntegrationProvider
from promise_shared.ids import new_id

"""Gmail as just another ContextSearchProvider (see providers.py/retriever.py) --
never a special code path inside ContextRetriever itself. These tests exercise
promise_app.tools.retrieve_commitment_context (the on-demand endpoint) and
promise_agent.steps.retrieval.retrieve_context (the orchestrator's own step)."""


def _connect_gmail(ctx, *, workspace_id: str, user_id: str, mock: MockGmailIntegrationProvider) -> IntegrationAccount:
    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=workspace_id, user_id=user_id, provider="gmail",
        account_identifier=f"{user_id}@example.com", status=IntegrationStatus.CONNECTED, secret_ref="gmail:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("gmail", lambda _account: mock)
    return account


def test_gmail_participates_in_retrieval_when_connected(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]

    mock = MockGmailIntegrationProvider(ctx.repos.drafts)
    mock.seed_messages([{
        "id": "gmail_m1", "workspace_id": ctx.default_workspace_id, "subject": "Re: proposal",
        "content": "Andi asked for one more revision", "sender": "andi@example.com", "recipient": "me@example.com",
        "occurred_at": "2026-01-01T00:00:00+00:00",
    }])
    _connect_gmail(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, mock=mock)

    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    sources = {item.source.system for item in result["results"]}
    assert "gmail" in sources


def test_gmail_does_not_participate_when_not_connected(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]

    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    sources = {item.source.system for item in result["results"]}
    assert "gmail" not in sources
    assert result["status"].value == "complete"


def test_gmail_failure_yields_partial_retrieval_never_fabricated_results(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]

    class _FailingGmail:
        provider_name = "gmail"

        def search_messages(self, workspace_id, query):
            raise RuntimeError("Gmail API unavailable")

        def get_message(self, workspace_id, message_id):
            raise RuntimeError("Gmail API unavailable")

        def search_files(self, workspace_id, query):
            return []

        def get_file(self, workspace_id, file_id):
            return None

        def create_draft(self, workspace_id, *, recipient, subject, body, attachment_file_id=None):
            raise NotImplementedError

        def send_message(self, workspace_id, *, draft_id, idempotency_key):
            raise NotImplementedError

    account = IntegrationAccount(
        id=new_id("ia"), workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, provider="gmail",
        account_identifier="andi@example.com", status=IntegrationStatus.CONNECTED, secret_ref="gmail:fake",
    )
    ctx.repos.integration_accounts.save(account)
    ctx.integrations.register_factory("gmail", lambda _account: _FailingGmail())

    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    assert result["status"].value == "partial"
    assert any(e["provider"] == "messages" for e in result["errors"])
    # local results still came back -- one provider failing doesn't wipe the rest
    assert len(result["results"]) > 0


def test_gmail_results_are_deduped_across_matching_search_terms(seeded_ctx):
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]

    mock = MockGmailIntegrationProvider(ctx.repos.drafts)
    mock.seed_messages([{
        "id": "gmail_m1", "workspace_id": ctx.default_workspace_id, "subject": "Andi proposal",
        "content": "andi proposal revision", "sender": "andi@example.com", "recipient": "me@example.com",
        "occurred_at": "2026-01-01T00:00:00+00:00",
    }])
    _connect_gmail(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, mock=mock)

    result = tools.retrieve_commitment_context(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    gmail_ids = [item.source.source_id for item in result["results"] if item.source.system == "gmail"]
    assert gmail_ids.count("gmail_m1") == 1


def test_orchestrator_retrieval_step_also_includes_gmail_when_connected(seeded_ctx):
    """Same wiring, but through the orchestrator's own retrieval step
    (promise_agent.steps.retrieval.retrieve_context), not the on-demand tool --
    this is what handle_commitment actually runs during the agent loop."""
    from promise_agent.steps.retrieval import retrieve_context

    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )["commitment"]

    mock = MockGmailIntegrationProvider(ctx.repos.drafts)
    mock.seed_messages([{
        "id": "gmail_m2", "workspace_id": ctx.default_workspace_id, "subject": "proposal notes",
        "content": "andi feedback on the proposal", "sender": "andi@example.com", "recipient": "me@example.com",
        "occurred_at": "2026-01-01T00:00:00+00:00",
    }])
    _connect_gmail(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, mock=mock)

    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id) if commitment.contact_id else None
    result = retrieve_context(commitment, contact, ctx.agent_repos)
    sources = {item.source.system for item in result["items"]}
    assert "gmail" in sources
