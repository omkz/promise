from __future__ import annotations

from typing import Any

from promise_app import tools
from promise_domain.models import Contact
from promise_shared.ids import new_id


class FakeIntegrationProvider:
    """A stand-in for a real Gmail/Drive provider. Implements the same
    IntegrationProvider shape as LocalIntegrationProvider but is backed by
    plain in-memory dicts instead of PROMISE's own document/message repos —
    proving the agent never depends on *which* provider it's talking to."""

    provider_name = "fake"

    def __init__(self) -> None:
        self.files = {"file_1": {"id": "file_1", "name": "Fake_Proposal.txt", "content_text": "hello world", "type": "text/plain"}}
        self.messages = {"msg_1": {"id": "msg_1", "content": "please add a timeline", "sender": "Fake", "recipient": "u", "subject": "s"}}
        self.drafts_created: list[dict[str, Any]] = []
        self.sent: list[str] = []

    def search_messages(self, workspace_id: str, query: str) -> list[dict]:
        return list(self.messages.values())

    def get_message(self, workspace_id: str, message_id: str) -> dict | None:
        return self.messages.get(message_id)

    def search_files(self, workspace_id: str, query: str) -> list[dict]:
        return list(self.files.values())

    def get_file(self, workspace_id: str, file_id: str) -> dict | None:
        return self.files.get(file_id)

    def create_draft(self, workspace_id: str, *, recipient, subject, body, attachment_file_id=None) -> dict:
        draft = {"id": "draft_fake_1", "recipient": recipient, "subject": subject, "body": body}
        self.drafts_created.append(draft)
        return draft

    def send_message(self, workspace_id: str, *, draft_id: str, idempotency_key: str) -> dict:
        self.sent.append(draft_id)
        return {"idempotent_replay": False, "draft": {"id": draft_id, "status": "sent"}}


def test_agent_retrieval_and_planning_work_against_a_swapped_provider(ctx):
    fake = FakeIntegrationProvider()
    ctx.integrations.register(fake)
    ctx.agent_repos.integration = fake  # same object the orchestrator holds

    # A verified email must already be on file for the send step to proceed — PROMISE
    # never invents one from a bare detected name (see promise_shared.errors.
    # VerifiedContactRequiredError). This is a real, explicitly-provided address, not a
    # system-fabricated one.
    ctx.repos.contacts.save(Contact(id=new_id("con"), workspace_id=ctx.default_workspace_id, name="Priya", email="priya@example.com"))

    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll send Priya the update today."
    )["commitment"]

    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    assert handled["action"].status.value == "waiting_for_approval"
    assert len(fake.drafts_created) == 1
    assert handled["draft"]["id"] == "draft_fake_1"

    tools.decide_approval(
        ctx, workspace_id=ctx.default_workspace_id, approval_id=handled["approval"].id, decision="approved", decided_by=ctx.default_user_id
    )
    tools.execute_approved_action(ctx, workspace_id=ctx.default_workspace_id, action_id=handled["action"].id, actor=ctx.default_user_id)

    assert fake.sent == ["draft_fake_1"]
