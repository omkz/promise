from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from promise_agent.commitment_extraction import (
    CommitmentExtractor,
    ExtractionCategory,
    MockCommitmentExtractionProvider,
)
from promise_agent.commitment_extraction.temporal import extract_temporal_phrase, resolve_due_at

REFERENCE_NOW = datetime(2026, 9, 23, 10, 0, tzinfo=ZoneInfo("Asia/Jakarta"))  # a Wednesday


def _extract(text: str, *, now: datetime = REFERENCE_NOW):
    return CommitmentExtractor(MockCommitmentExtractionProvider()).extract(text, now=now, timezone="Asia/Jakarta")


# ---- the mock provider's fixtures from the Commitment Detection Engine spec -----------------

@pytest.mark.parametrize(
    "text",
    [
        "I'll send Andi the revised proposal tomorrow morning.",
        "I'll call Sarah Friday afternoon.",
        "I need to call Sarah on Friday.",
        "I promised John I'd review the contract tonight.",
        "I'll follow up with the client next week.",
    ],
)
def test_should_produce_commitments(text):
    result = _extract(text)
    assert result.is_commitment is True
    assert result.category == ExtractionCategory.COMMITMENT
    assert result.confidence > 0.9


@pytest.mark.parametrize(
    "text",
    [
        "Maybe I should review the contract next week.",
        "Andi will send me the proposal tomorrow.",
        "Can you send Andi the proposal?",
        "John said he'll send it tomorrow.",
        "I already sent Andi the proposal yesterday.",
        "Maybe I should call Sarah.",
        "We should probably review this sometime.",
    ],
)
def test_should_not_produce_commitments(text):
    result = _extract(text)
    assert result.is_commitment is False


def test_third_person_gets_its_own_category_not_a_false_commitment():
    result = _extract("Andi will send me the proposal tomorrow.")
    assert result.category == ExtractionCategory.OTHER_PERSON_OBLIGATION


def test_quoted_speech_gets_its_own_category():
    result = _extract("John said he'll send it tomorrow.")
    assert result.category == ExtractionCategory.QUOTED


def test_question_gets_its_own_category():
    result = _extract("Can you send Andi the proposal?")
    assert result.category == ExtractionCategory.QUESTION


def test_past_action_gets_its_own_category():
    result = _extract("I already sent Andi the proposal yesterday.")
    assert result.category == ExtractionCategory.PAST_ACTION


def test_contact_extraction():
    result = _extract("I'll send Andi the revised proposal tomorrow morning.")
    assert result.contact_name == "Andi"


def test_no_contact_when_none_is_named():
    result = _extract("I'll finish the report today.")
    assert result.contact_name is None


def test_confidence_is_normalized_between_zero_and_one():
    for text in ["I'll send Andi the proposal tomorrow.", "Can you send Andi the proposal?", ""]:
        result = _extract(text)
        assert 0.0 <= result.confidence <= 1.0


def test_ambiguous_bare_imperative_is_low_confidence_not_silently_dropped_or_captured():
    """"Review the contract next week." (no first-person subject) is the spec's own
    example of a statement that should come back needing confirmation, not a flat
    yes/no — see CONFIDENCE in the Commitment Detection Engine spec."""
    result = _extract("Review the contract next week.")
    assert result.is_commitment is True
    assert result.confidence < 0.80


def test_source_excerpt_and_provenance_are_preserved():
    text = "I'll send Andi the revised proposal tomorrow morning."
    result = _extract(text)
    assert result.source_excerpt == text
    assert result.provider_name == "mock"


# ---- temporal resolution ---------------------------------------------------------------------

def test_extract_temporal_phrase_finds_relative_dates():
    assert extract_temporal_phrase("I'll send it tomorrow morning.") == "tomorrow morning"
    assert extract_temporal_phrase("I'll call Sarah Friday afternoon.") == "Friday afternoon"
    assert extract_temporal_phrase("Let's talk next week.") == "next week"
    assert extract_temporal_phrase("No date here.") is None


def test_resolve_due_at_tomorrow_morning_uses_documented_default_hour():
    resolved = resolve_due_at("tomorrow morning", now=REFERENCE_NOW)
    dt = datetime.fromisoformat(resolved)
    assert dt.date() == (REFERENCE_NOW.date().replace(day=REFERENCE_NOW.day + 1))
    assert dt.hour == 9  # documented default morning hour


def test_resolve_due_at_is_timezone_aware():
    resolved = resolve_due_at("tomorrow morning", now=REFERENCE_NOW)
    dt = datetime.fromisoformat(resolved)
    assert dt.tzinfo is not None
    assert dt.utcoffset() == REFERENCE_NOW.utcoffset()


def test_resolve_due_at_weekday_resolves_to_the_next_occurrence():
    # REFERENCE_NOW is a Wednesday; "Friday" should land 2 days later, not this instant.
    resolved = resolve_due_at("friday afternoon", now=REFERENCE_NOW)
    dt = datetime.fromisoformat(resolved)
    assert dt.weekday() == 4  # Friday
    assert dt > REFERENCE_NOW
    assert dt.hour == 14  # documented default afternoon hour


def test_resolve_due_at_naming_todays_own_weekday_means_next_week():
    wednesday = REFERENCE_NOW
    resolved = resolve_due_at("wednesday", now=wednesday)
    dt = datetime.fromisoformat(resolved)
    assert (dt.date() - wednesday.date()).days == 7


def test_resolve_due_at_none_when_no_phrase():
    assert resolve_due_at(None, now=REFERENCE_NOW) is None


def test_extractor_receives_an_explicit_reference_clock_not_the_system_clock():
    """The extractor must never fall back to a hidden system-clock read when a
    caller supplies `now` — two different reference times must produce
    different, deterministic due dates for the same relative phrase."""
    early = _extract("I'll call Sam tomorrow.", now=datetime(2026, 1, 1, 8, 0, tzinfo=ZoneInfo("Asia/Jakarta")))
    later = _extract("I'll call Sam tomorrow.", now=datetime(2026, 6, 1, 8, 0, tzinfo=ZoneInfo("Asia/Jakarta")))
    assert early.due_at != later.due_at
    assert early.due_at.startswith("2026-01-02")
    assert later.due_at.startswith("2026-06-02")
