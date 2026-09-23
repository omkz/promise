from __future__ import annotations

import pytest
from promise_app import tools
from promise_domain.models import Contact, Document
from promise_shared.errors import VerifiedContactRequiredError
from promise_shared.ids import new_id

"""Regression tests: PROMISE must never invent a contact email (e.g. `andi@example.com`
from the bare name "Andi", or a placeholder like `recipient@example.com`), and any
external send action needs a verified email or must stop with a clear error."""


def test_contact_created_from_detected_name_has_no_invented_email(ctx):
    result = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll send Priya the update today."
    )
    contact = result["contact"]
    assert contact is not None and contact.name == "Priya"
    assert contact.email is None


def test_seeded_demo_contact_keeps_its_explicit_email(seeded_ctx):
    """Andi/Sarah are seed data with real, explicitly-provided emails
    (packages/app/promise_app/seed.py) — the fix must not touch those."""
    result = tools.create_commitment(
        seeded_ctx, workspace_id=seeded_ctx.default_workspace_id, user_id=seeded_ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )
    assert result["contact"].email == "andi@example.com"


def test_create_draft_requires_a_verified_email(ctx):
    contact = ctx.repos.contacts.save(Contact(id=new_id("con"), workspace_id=ctx.default_workspace_id, name="NoEmail"))
    document = ctx.repos.documents.save(
        Document(id=new_id("doc"), workspace_id=ctx.default_workspace_id, name="d.txt", type="text/plain", content_text="hi")
    )
    with pytest.raises(VerifiedContactRequiredError):
        tools.create_draft(ctx, workspace_id=ctx.default_workspace_id, contact_id=contact.id, document_id=document.id)


def test_create_draft_works_once_a_verified_email_is_on_file(ctx):
    contact = ctx.repos.contacts.save(
        Contact(id=new_id("con"), workspace_id=ctx.default_workspace_id, name="HasEmail", email="has-email@example.com")
    )
    document = ctx.repos.documents.save(
        Document(id=new_id("doc"), workspace_id=ctx.default_workspace_id, name="d.txt", type="text/plain", content_text="hi")
    )
    draft = tools.create_draft(ctx, workspace_id=ctx.default_workspace_id, contact_id=contact.id, document_id=document.id)
    assert draft["recipient"] == "has-email@example.com"


def test_handle_commitment_stops_with_verified_contact_required_when_contact_has_no_email(seeded_ctx):
    """"Priya" isn't seeded, so create_commitment makes a bare Contact with no email.
    The Andi proposal doc is still findable (retrieval falls back to a "proposal"
    search), so this must fail on the missing-email check specifically, not on
    "no relevant document found"."""
    ctx = seeded_ctx
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll send Priya the update today."
    )["commitment"]

    with pytest.raises(VerifiedContactRequiredError) as excinfo:
        tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert excinfo.value.contact_name == "Priya"


def test_handle_commitment_with_no_named_contact_also_stops_rather_than_using_a_placeholder(ctx):
    """No contact at all (e.g. "I'll finish the report today.") must never fall back to
    a placeholder address like recipient@example.com — even though a relevant document
    (matched by the commitment's own derived keywords, "report") exists and retrieval
    finds it fine."""
    ctx.repos.documents.save(
        Document(
            id=new_id("doc"), workspace_id=ctx.default_workspace_id, name="Weekly_Report.txt",
            type="text/plain", content_text="report content", metadata={"tags": ["report"]},
        )
    )
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll finish the report today."
    )["commitment"]
    assert commitment.contact_id is None

    with pytest.raises(VerifiedContactRequiredError) as excinfo:
        tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert excinfo.value.contact_id is None
    assert excinfo.value.contact_name is None
