from __future__ import annotations

import os
import re
from typing import Protocol

from promise_domain.enums import Priority
from promise_shared.errors import ExtractionProviderError
from pydantic import ValidationError

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .schema import ExtractionCategory, RawCommitmentExtraction

"""Commitment-extraction providers.

`CommitmentExtractionProvider` is the seam that keeps `CommitmentExtractor`
ignorant of Bedrock: `MockCommitmentExtractionProvider` is a deterministic,
rule-based classifier (no network, no AWS credentials) used whenever
`BEDROCK_ENABLED` is false, which is every local dev run and CI job.
`BedrockCommitmentExtractionProvider` calls Amazon Bedrock and is only ever
constructed when `BEDROCK_ENABLED=true`.
"""

_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}

_QUESTION_STARTERS = (
    "can you", "could you", "would you", "will you", "do you", "did you", "are you", "have you",
)
_HYPOTHETICAL_MARKERS = ("maybe", "perhaps", "might ", "should probably", "we should", "we could", "we might")
_PAST_MARKERS = ("yesterday", "already", "last week", "last night")
_PAST_VERBS = ("sent", "called", "finished", "completed", "submitted", "delivered", "reviewed", "emailed", "shared")
_COMMITMENT_MARKERS = (
    "i'll", "i will", "i need to", "i've got to", "i have to", "i'm going to", "i am going to",
    "i plan to", "i promise", "i promised",
)
_ACTION_VERBS = (
    "send", "email", "call", "share", "deliver", "submit", "finish", "review", "follow up", "check", "confirm",
    "meet", "meet with", "schedule",
)

_CONTACT_PATTERN = re.compile(
    r"\b(?:to|send|email|call|share|deliver|submit|promised|tell|give|meet|meet with|schedule)\s+([A-Z][a-zA-Z'-]+)\b"
)
_OTHER_SUBJECT_PATTERN = re.compile(r"^([A-Z][a-zA-Z'-]+)\s+(?:will|said|says|told me|is going to|promised)\b")
_ACTION_VERB_PATTERN = re.compile(r"\b(" + "|".join(_ACTION_VERBS) + r")\b", re.IGNORECASE)
_LEADING_ARTICLE_PATTERN = re.compile(r"^(\w+)\s+(the|a|an)\s+", re.IGNORECASE)


class CommitmentExtractionProvider(Protocol):
    """A pluggable source of raw commitment classifications.

    Implementations never touch persistence, FastAPI, or MCP — they only turn
    text into a `RawCommitmentExtraction`. `CommitmentExtractor` is the only
    caller.
    """

    provider_name: str
    model_id: str | None

    def extract(self, text: str) -> RawCommitmentExtraction:
        """Raises `promise_shared.errors.ExtractionProviderError` on failure —
        never returns `None` or a fabricated result. A caller that wants a
        deterministic fallback must catch that explicitly and choose to; it
        never happens implicitly inside a provider."""
        ...


def _find_contact(text: str) -> str | None:
    for match in _CONTACT_PATTERN.finditer(text):
        name = match.group(1)
        if name.lower() not in _WEEKDAYS:
            return name
    return None


def _derive_action(text: str, contact_name: str | None, temporal_phrase: str | None) -> str | None:
    match = _ACTION_VERB_PATTERN.search(text)
    if not match:
        return None
    phrase = text[match.start():]
    if contact_name:
        phrase = re.sub(rf"\b{re.escape(contact_name)}\b", "", phrase)
    if temporal_phrase:
        phrase = phrase.replace(temporal_phrase, "")
    phrase = re.sub(r"\s+", " ", phrase).strip(" .!?,")
    phrase = _LEADING_ARTICLE_PATTERN.sub(r"\1 ", phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    return (phrase[:1].upper() + phrase[1:]) if phrase else None


class MockCommitmentExtractionProvider:
    """Deterministic, rule-based provider. No network access, no AWS
    credentials required — the default whenever `BEDROCK_ENABLED` is false,
    and the fallback whenever the Bedrock provider is skipped or fails.
    """

    provider_name = "mock"
    model_id: str | None = None

    def extract(self, text: str) -> RawCommitmentExtraction:
        from .temporal import extract_temporal_phrase

        clean = text.strip()
        lower = clean.lower()
        temporal_phrase = extract_temporal_phrase(clean)
        contact_name = _find_contact(clean)

        def outcome(
            category: ExtractionCategory, is_commitment: bool, confidence: float,
            *, action: str | None = None, priority: Priority | None = None,
        ) -> RawCommitmentExtraction:
            return RawCommitmentExtraction(
                is_commitment=is_commitment, category=category, action=action, contact_name=contact_name,
                temporal_expression=temporal_phrase, priority_hint=priority, confidence=confidence,
                reasoning=f"mock provider matched rule for category={category.value}",
            )

        if not clean:
            return outcome(ExtractionCategory.NONE, False, 0.0)

        if clean.endswith("?") or lower.startswith(_QUESTION_STARTERS):
            return outcome(ExtractionCategory.QUESTION, False, 0.9)

        other_subject = _OTHER_SUBJECT_PATTERN.match(clean)
        if other_subject and other_subject.group(1).lower() != "i":
            category = ExtractionCategory.QUOTED if ("said" in lower or "told" in lower) else ExtractionCategory.OTHER_PERSON_OBLIGATION
            return outcome(category, False, 0.9)

        if any(marker in lower for marker in _PAST_MARKERS) and any(verb in lower for verb in _PAST_VERBS):
            return outcome(ExtractionCategory.PAST_ACTION, False, 0.85)

        if any(marker in lower for marker in _HYPOTHETICAL_MARKERS):
            return outcome(ExtractionCategory.HYPOTHETICAL, False, 0.4)

        urgent = any(k in lower for k in ("tomorrow", "today", "tonight", "urgent", "asap"))
        priority = Priority.HIGH if urgent else Priority.MEDIUM

        if any(marker in lower for marker in _COMMITMENT_MARKERS):
            action = _derive_action(clean, contact_name, temporal_phrase)
            return outcome(ExtractionCategory.COMMITMENT, True, 0.97, action=action, priority=priority)

        if lower.startswith(_ACTION_VERBS):
            # A bare imperative with no first-person subject ("Review the contract next week.") reads
            # like a personal note-to-self, but it's genuinely ambiguous — surface it for confirmation
            # rather than either silently capturing or silently dropping it.
            action = _derive_action(clean, contact_name, temporal_phrase)
            return outcome(ExtractionCategory.COMMITMENT, True, 0.55, action=action, priority=priority)

        return outcome(ExtractionCategory.NONE, False, 0.3)


# Bedrock/botocore error codes worth retrying as-is (throttling, transient service issues).
_RETRYABLE_ERROR_CODES = {
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "InternalServerException", "ModelTimeoutException", "ModelNotReadyException",
}
# Error codes that will fail again identically on retry (bad request, auth, missing model).
_NON_RETRYABLE_ERROR_CODES = {
    "ValidationException", "AccessDeniedException", "ResourceNotFoundException",
    "ModelErrorException", "UnrecognizedClientException",
}


def _is_retryable(exc: Exception) -> bool:
    error_code = None
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error_code = response.get("Error", {}).get("Code")
    if error_code in _NON_RETRYABLE_ERROR_CODES:
        return False
    if error_code in _RETRYABLE_ERROR_CODES:
        return True
    # Unknown/transport-level failures (timeouts, connection errors, DNS, ...) default to
    # retryable: safer to let the caller retry a possibly-transient failure than to treat
    # every unrecognized error as permanent.
    return True


class BedrockCommitmentExtractionProvider:
    """Real extraction via Amazon Bedrock's Converse API, using tool-use to force
    a validated JSON object matching `RawCommitmentExtraction` — never
    free-form text parsing.

    On failure this raises `ExtractionProviderError` — it never silently
    returns a mock/fabricated result. `BEDROCK_ENABLED=true` is an explicit
    choice to require a real model; if that model is unreachable or
    misbehaves, the caller must see a controlled, classified error (retryable
    vs. not) rather than an extraction that quietly used different logic than
    the one that was configured.

    Note: this project has not adopted the Strands agent framework anywhere in
    its stack (`packages/agent/promise_agent/llm.py` already calls Bedrock
    directly via `boto3`'s `bedrock-runtime` client, and that's the pattern
    followed here); introducing a new agent framework as a dependency for a
    single provider would be a larger architectural change than this task
    calls for. Converse's tool-use forces the model to emit an argument object
    that satisfies a JSON Schema, which is validated through the same
    `RawCommitmentExtraction` Pydantic model either way — the guarantee Strands'
    `structured_output_model` gives you, without a new framework dependency.
    """

    provider_name = "bedrock"

    def __init__(self, *, model_id: str | None = None, region: str | None = None) -> None:
        self.model_id = model_id or os.getenv("COMMITMENT_EXTRACTION_MODEL_ID", os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"))
        self._region = region or os.getenv("AWS_REGION", "us-east-1")

    def extract(self, text: str) -> RawCommitmentExtraction:
        try:
            import boto3
        except ImportError as exc:
            raise ExtractionProviderError(self.provider_name, f"boto3 is not available: {exc}", retryable=False) from exc

        try:
            client = boto3.client("bedrock-runtime", region_name=self._region)
            tool_spec = {
                "toolSpec": {
                    "name": "record_commitment_extraction",
                    "description": "Record the structured classification of the input statement.",
                    "inputSchema": {"json": RawCommitmentExtraction.model_json_schema()},
                }
            }
            response = client.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": build_user_prompt(text)}]}],
                toolConfig={"tools": [tool_spec], "toolChoice": {"tool": {"name": "record_commitment_extraction"}}},
                inferenceConfig={"temperature": 0.0, "maxTokens": 600},
            )
        except Exception as exc:
            raise ExtractionProviderError(self.provider_name, str(exc), retryable=_is_retryable(exc)) from exc

        for block in response["output"]["message"]["content"]:
            if "toolUse" in block:
                try:
                    return RawCommitmentExtraction.model_validate(block["toolUse"]["input"])
                except ValidationError as exc:
                    raise ExtractionProviderError(
                        self.provider_name, f"model returned an invalid structured extraction: {exc}", retryable=False
                    ) from exc
        raise ExtractionProviderError(self.provider_name, "model returned no structured tool-use output", retryable=False)
