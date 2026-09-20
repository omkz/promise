from __future__ import annotations

import pytest
from promise_domain.models import Commitment, Contact
from promise_shared.errors import ConflictError
from promise_shared.ids import new_id


def test_optimistic_concurrency_conflict(ctx):
    commitment = Commitment(
        id=new_id("com"), workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        action="send it", title="send it", description="send it",
    )
    ctx.repos.commitments.save(commitment)
    assert commitment.version == 1

    ctx.repos.commitments.update(ctx.default_workspace_id, commitment.id, lambda c: setattr(c, "title", "v2"), expected_version=1)

    with pytest.raises(ConflictError):
        ctx.repos.commitments.update(ctx.default_workspace_id, commitment.id, lambda c: setattr(c, "title", "v3-stale"), expected_version=1)


def test_soft_delete_hides_from_list_but_not_from_get(ctx):
    contact = Contact(id=new_id("con"), workspace_id=ctx.default_workspace_id, name="Temp")
    ctx.repos.contacts.save(contact)
    assert contact.id in [c.id for c in ctx.repos.contacts.list(ctx.default_workspace_id)]

    ctx.repos.contacts.soft_delete(ctx.default_workspace_id, contact.id)

    assert contact.id not in [c.id for c in ctx.repos.contacts.list(ctx.default_workspace_id)]
    still_there = ctx.repos.contacts.get(ctx.default_workspace_id, contact.id)
    assert still_there is not None and still_there.deleted_at is not None


def test_local_integration_provider_send_is_idempotent(seeded_ctx):
    ctx = seeded_ctx
    provider = ctx.integrations.get()
    draft = provider.create_draft(ctx.default_workspace_id, recipient="a@b.com", subject="s", body="b")

    first = provider.send_message(ctx.default_workspace_id, draft_id=draft["id"], idempotency_key="k1")
    second = provider.send_message(ctx.default_workspace_id, draft_id=draft["id"], idempotency_key="k1")

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
