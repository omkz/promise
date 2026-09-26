from __future__ import annotations

import json
import os
from typing import Any

from promise_shared.errors import LLMProviderError

"""Bedrock-backed document revision, behind a small provider abstraction.

`BEDROCK_ENABLED=false` (all local dev + CI): `revise_document` uses the
deterministic `mock_revision` directly — not as a failure fallback, but as
the configured behavior.

`BEDROCK_ENABLED=true`: `revise_document` calls Bedrock and, on any failure
(network, throttling, malformed response, ...), raises `LLMProviderError`
rather than silently substituting the mock. An explicit choice to require a
real model must fail clearly and retryably when that model is unavailable —
never mask a real Bedrock failure as a successful revision.
"""

_RETRYABLE_ERROR_CODES = {
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "InternalServerException", "ModelTimeoutException", "ModelNotReadyException",
}
_NON_RETRYABLE_ERROR_CODES = {
    "ValidationException", "AccessDeniedException", "ResourceNotFoundException",
    "ModelErrorException", "UnrecognizedClientException",
}


def _is_retryable(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    error_code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    if error_code in _NON_RETRYABLE_ERROR_CODES:
        return False
    if error_code in _RETRYABLE_ERROR_CODES:
        return True
    return True  # unknown/transport-level failures default to retryable


def bedrock_converse(prompt: str) -> str:
    """Call Amazon Bedrock. Only ever called when `BEDROCK_ENABLED=true` (see
    `revise_document`) — raises `LLMProviderError` on any failure; never
    returns `None` or a fabricated response."""
    try:
        import boto3
        from promise_shared.aws_config import boto_config
    except ImportError as exc:
        raise LLMProviderError("bedrock", f"boto3 is not available: {exc}", retryable=False) from exc

    try:
        client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"), config=boto_config())
        response = client.converse(
            modelId=os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"),
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0.2, "maxTokens": 1800},
        )
        return response["output"]["message"]["content"][0]["text"]
    except Exception as exc:
        raise LLMProviderError("bedrock", str(exc), retryable=_is_retryable(exc)) from exc


def revise_document(original: str, feedback: str) -> tuple[str, list[str]]:
    """Produce a revised document + change summary.

    `BEDROCK_ENABLED=false` -> deterministic `mock_revision`, always.
    `BEDROCK_ENABLED=true` -> real Bedrock call; raises `LLMProviderError` on
    failure or an invalid/unparseable response — never falls back to the mock.
    """
    if os.getenv("BEDROCK_ENABLED", "false").lower() != "true":
        return mock_revision(original, feedback)

    prompt = (
        "You are a careful document editor. Revise the document using the feedback below.\n\n"
        f"ORIGINAL:\n{original}\n\nFEEDBACK:\n{feedback}\n\n"
        'Return JSON with keys `revised_text` and `changes` (array of short strings). '
        "Do not invent facts."
    )
    text = bedrock_converse(prompt)  # raises LLMProviderError on failure; never returns None here
    try:
        payload: dict[str, Any] = json.loads(text)
        return str(payload["revised_text"]), [str(x) for x in payload.get("changes", [])]
    except Exception as exc:
        raise LLMProviderError("bedrock", f"model returned an unparseable revision payload: {exc}", retryable=False) from exc


def mock_revision(original: str, feedback: str) -> tuple[str, list[str]]:
    """Deterministic revision used whenever `BEDROCK_ENABLED=false` (all local dev
    and CI) — not a failure fallback, a directly-selected, always-available path."""
    changes: list[str] = []
    lower_feedback = feedback.lower()
    if "pricing" in lower_feedback:
        changes.append("Updated pricing section based on the latest feedback")
    if "timeline" in lower_feedback:
        changes.append("Added implementation timeline")
    if not changes:
        changes.append("Applied the latest feedback")
    revised = original + "\n\n--- REVISION NOTES ---\n" + "\n".join(f"- {c}" for c in changes)
    return revised, changes
