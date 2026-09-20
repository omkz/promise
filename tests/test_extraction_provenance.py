from __future__ import annotations

from promise_app import tools


def test_extraction_creates_commitment_with_contact_and_due_date(ctx):
    result = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id,
        text="I'll send Andi the revised proposal tomorrow morning.",
    )
    commitment = result["commitment"]
    contact = result["contact"]

    assert commitment.status.value == "open"
    assert contact is not None and contact.name == "Andi"
    assert commitment.due_at is not None
    assert commitment.priority.value == "high"  # "tomorrow" implies urgency


def test_source_provenance_answers_why_do_you_think_i_promised_this(ctx):
    text = "I'll send Andi the revised proposal tomorrow morning."
    result = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text=text, source_system="alexa",
    )
    commitment = result["commitment"]
    source = result["source"]

    assert source.commitment_id == commitment.id
    assert source.source_type == "conversation"
    assert source.source_system == "alexa"
    assert source.excerpt == text
    assert 0.0 < source.confidence <= 1.0
    assert commitment.source_id == source.id

    stored_source = ctx.repos.sources.require(ctx.default_workspace_id, source.id)
    assert stored_source.excerpt == text


def test_extraction_without_a_named_person_still_creates_commitment(ctx):
    result = tools.create_commitment(
        ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="I'll finish the report today.",
    )
    assert result["contact"] is None
    assert result["commitment"].contact_id is None
