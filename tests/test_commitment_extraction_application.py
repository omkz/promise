from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from promise_app import tools

REFERENCE_NOW = datetime(2026, 9, 23, 10, 0, tzinfo=ZoneInfo("Asia/Jakarta"))  # a Wednesday


def _create(ctx, text, **kwargs):
    return tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text=text, **kwargs)


# ---- integration test from the Commitment Detection Engine spec -------------------------------

def test_integration_send_revised_proposal_tomorrow_morning(ctx):
    before = len(tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id))

    result = _create(ctx, "I'll send Andi the revised proposal tomorrow morning.")

    assert result["detected"] is True
    assert result["persisted"] is True
    commitment = result["commitment"]
    contact = result["contact"]
    source = result["source"]

    assert contact is not None and contact.name == "Andi"
    assert "revised proposal" in commitment.action.lower()
    assert commitment.due_at is not None
    assert source.excerpt == "I'll send Andi the revised proposal tomorrow morning."

    after = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id)
    assert len(after) == before + 1


# ---- non-commitments never create a Commitment row ---------------------------------------------

def test_non_commitment_is_not_persisted(ctx):
    before = len(tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id))

    result = _create(ctx, "Can you send Andi the proposal?")

    assert result["detected"] is False
    assert result["persisted"] is False
    assert result["commitment"] is None
    assert "reason" in result and result["reason"]

    after = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id)
    assert len(after) == before


def test_other_persons_obligation_is_not_persisted(ctx):
    result = _create(ctx, "Andi will send me the proposal tomorrow.")
    assert result["detected"] is False
    assert result["persisted"] is False


def test_past_action_is_not_persisted(ctx):
    result = _create(ctx, "I already sent Andi the proposal yesterday.")
    assert result["detected"] is False
    assert result["persisted"] is False


# ---- confidence threshold / needs_confirmation --------------------------------------------------

def test_low_confidence_detection_needs_confirmation_and_is_not_persisted(ctx):
    before = len(tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id))

    result = _create(ctx, "Review the contract next week.")

    assert result["detected"] is True
    assert result["needs_confirmation"] is True
    assert result["persisted"] is False
    assert result["commitment"] is None
    assert "message" in result and "Capture it?" in result["message"]

    after = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id)
    assert len(after) == before


def test_confirming_a_low_confidence_detection_persists_it(ctx):
    result = _create(ctx, "Review the contract next week.", confirm=True)

    assert result["detected"] is True
    assert result["needs_confirmation"] is False
    assert result["persisted"] is True
    assert result["commitment"] is not None


def test_auto_capture_threshold_is_configurable(ctx, monkeypatch):
    monkeypatch.setenv("COMMITMENT_AUTO_CAPTURE_THRESHOLD", "0.10")
    result = _create(ctx, "Review the contract next week.")
    assert result["persisted"] is True  # 0.55 mock confidence now clears the lowered bar


# ---- duplicate prevention -------------------------------------------------------------------

def test_duplicate_commitment_is_not_created_twice(ctx):
    text = "I'll send Andi the revised proposal tomorrow morning."

    first = _create(ctx, text)
    second = _create(ctx, text)

    assert first["commitment"].id == second["commitment"].id
    assert second["duplicate"] is True

    rows = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id)
    assert len(rows) == 1


def test_different_due_dates_are_not_treated_as_duplicates(ctx):
    first = _create(ctx, "I'll send Andi the revised proposal tomorrow morning.")
    second = _create(ctx, "I'll send Andi the revised proposal next week.")

    assert first["commitment"].id != second["commitment"].id
    rows = tools.search_commitments(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id)
    assert len(rows) == 2


# ---- provenance -----------------------------------------------------------------------------

def test_extraction_provenance_answers_why_do_you_think_i_promised_this(ctx):
    text = "I need to call Sarah on Friday."
    result = _create(ctx, text, source_system="alexa")

    assert result["extraction"].source_excerpt == text
    assert result["extraction"].provider_name == "mock"
    assert result["source"].excerpt == text
    assert result["source"].source_system == "alexa"
    assert 0.0 < result["extraction"].confidence <= 1.0


# ---- mock provider works with no AWS credentials / network ------------------------------------

def test_mock_provider_used_by_default_without_bedrock_enabled(ctx, monkeypatch):
    monkeypatch.delenv("BEDROCK_ENABLED", raising=False)
    result = _create(ctx, "I'll call Sam today.")
    assert result["extraction"].provider_name == "mock"


# ---- timezone handling ------------------------------------------------------------------------

def test_timezone_is_configurable_via_promise_timezone(ctx, monkeypatch):
    monkeypatch.setenv("PROMISE_TIMEZONE", "America/New_York")
    result = _create(ctx, "I'll call Sam tomorrow morning.")
    due_at = result["commitment"].due_at
    assert due_at is not None
    resolved = datetime.fromisoformat(due_at)
    assert resolved.utcoffset() == ZoneInfo("America/New_York").utcoffset(resolved)
