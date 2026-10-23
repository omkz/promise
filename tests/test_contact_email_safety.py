from __future__ import annotations

import pytest
from promise_app import tools
from promise_domain.enums import ActionStatus, ApprovalStatus
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
    """No contact at all, on an otherwise-plannable send-type commitment (e.g. "I'll
    send the weekly report today."), must never fall back to a placeholder address
    like recipient@example.com — even though a relevant document (matched by the
    commitment's own derived keywords, "report") exists and retrieval finds it fine."""
    ctx.repos.documents.save(
        Document(
            id=new_id("doc"), workspace_id=ctx.default_workspace_id, name="Weekly_Report.txt",
            type="text/plain", content_text="report content", metadata={"tags": ["report"]},
        )
    )
    commitment = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll send the weekly report today."
    )["commitment"]
    assert commitment.contact_id is None

    with pytest.raises(VerifiedContactRequiredError) as excinfo:
        tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert excinfo.value.contact_id is None
    assert excinfo.value.contact_name is None


def test_duplicate_contacts_prefers_unambiguous_contact_with_email_over_bare_contact(seeded_ctx):
    """When a bare contact (no email) and a verified contact (with email) share the same name,
    PROMISE must unambiguously choose the contact with the email rather than picking the bare
    contact by list-order accident."""
    ctx = seeded_ctx
    # Simulate a prior bare contact creation
    bare_contact = ctx.repos.contacts.save(
        Contact(
            id=new_id("con"),
            workspace_id=ctx.default_workspace_id,
            name="Alex",
            email=None,
            created_at="2026-01-01T00:00:00Z",
        )
    )

    result = tools.create_commitment(
        ctx,
        workspace_id=ctx.default_workspace_id,
        user_id=ctx.default_user_id,
        text="I will send Alex the proposal tomorrow.",
    )
    resolved_contact = result["contact"]
    assert resolved_contact is not None
    assert resolved_contact.id == "con_alex"
    assert resolved_contact.email == "alex@example.com"
    assert resolved_contact.id != bare_contact.id

    # Handling this commitment succeeds and plans an action requiring approval,
    # rather than failing with VerifiedContactRequiredError.
    handled = tools.handle_commitment(
        ctx,
        workspace_id=ctx.default_workspace_id,
        user_id=ctx.default_user_id,
        commitment_id=result["commitment"].id,
    )
    assert handled["action"] is not None
    assert handled["action"].status == ActionStatus.WAITING_FOR_APPROVAL
    assert handled["approval"].status == ApprovalStatus.PENDING


def test_duplicate_contacts_order_independent_resolution(ctx):
    """Whether the bare contact or email contact appears first in repository listing,
    the unambiguous email contact is selected."""
    ws = ctx.default_workspace_id
    bare = ctx.repos.contacts.save(
        Contact(id="con_1_bare", workspace_id=ws, name="Taylor", email=None, created_at="2026-01-01T00:00:00Z")
    )
    with_email = ctx.repos.contacts.save(
        Contact(id="con_2_email", workspace_id=ws, name="Taylor", email="taylor@example.com", created_at="2026-01-02T00:00:00Z")
    )

    result = tools.create_commitment(ctx, workspace_id=ws, user_id=ctx.default_user_id, text="I'll email Taylor the report.")
    assert result["contact"].id == with_email.id
    assert result["contact"].id != bare.id
    assert result["contact"].email == "taylor@example.com"


def test_ambiguous_recipients_with_different_emails_never_selects_arbitrary_email(ctx):
    """When multiple contacts with the same name have *different* email addresses,
    PROMISE must refuse to guess among them and must NOT attach either email address.
    Downstream action execution must fail closed with VerifiedContactRequiredError."""
    ws = ctx.default_workspace_id
    ctx.repos.contacts.save(
        Contact(id="con_alex_1", workspace_id=ws, name="Alex", email="alex.work@example.com", created_at="2026-01-01T00:00:00Z")
    )
    ctx.repos.contacts.save(
        Contact(id="con_alex_2", workspace_id=ws, name="Alex", email="alex.personal@example.com", created_at="2026-01-02T00:00:00Z")
    )
    ctx.repos.documents.save(
        Document(
            id=new_id("doc"),
            workspace_id=ws,
            name="Proposal.pdf",
            type="application/pdf",
            content_text="Proposal content",
            metadata={"tags": ["proposal"]},
        )
    )

    result = tools.create_commitment(ctx, workspace_id=ws, user_id=ctx.default_user_id, text="I will send Alex the proposal tomorrow.")
    contact = result["contact"]
    assert contact is not None
    assert contact.email is None  # Safe: did not guess or pick either email

    with pytest.raises(VerifiedContactRequiredError) as excinfo:
        tools.handle_commitment(ctx, workspace_id=ws, user_id=ctx.default_user_id, commitment_id=result["commitment"].id)
    assert excinfo.value.contact_name == "Alex"


def test_duplicate_contacts_with_identical_emails_resolves_deterministically(ctx):
    """If duplicate contact records exist with identical email addresses,
    resolution deterministically selects the oldest record."""
    ws = ctx.default_workspace_id
    c1 = ctx.repos.contacts.save(
        Contact(id="con_older", workspace_id=ws, name="Morgan", email="morgan@example.com", created_at="2026-01-01T00:00:00Z")
    )
    ctx.repos.contacts.save(
        Contact(id="con_newer", workspace_id=ws, name="Morgan", email="morgan@example.com", created_at="2026-01-02T00:00:00Z")
    )

    result = tools.create_commitment(ctx, workspace_id=ws, user_id=ctx.default_user_id, text="I will send Morgan the notes.")
    assert result["contact"].id == c1.id
    assert result["contact"].email == "morgan@example.com"


def test_duplicate_bare_contacts_reuses_existing_deterministically_without_creating_new_record(ctx):
    """When only bare contacts without emails exist, resolution deterministically reuses
    the existing record instead of creating unbounded duplicate rows."""
    ws = ctx.default_workspace_id
    c1 = ctx.repos.contacts.save(
        Contact(id="con_bare_old", workspace_id=ws, name="Robin", email=None, created_at="2026-01-01T00:00:00Z")
    )
    ctx.repos.contacts.save(
        Contact(id="con_bare_new", workspace_id=ws, name="Robin", email=None, created_at="2026-01-02T00:00:00Z")
    )
    initial_count = len(ctx.repos.contacts.list(ws))

    result = tools.create_commitment(ctx, workspace_id=ws, user_id=ctx.default_user_id, text="I will send Robin the summary.")
    assert result["contact"].id == c1.id
    assert result["contact"].email is None
    assert len(ctx.repos.contacts.list(ws)) == initial_count
