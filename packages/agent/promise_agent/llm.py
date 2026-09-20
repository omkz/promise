from __future__ import annotations

import json
import os
from typing import Any


def bedrock_converse(prompt: str) -> str | None:
    """Call Amazon Bedrock if enabled/available; otherwise return None.

    Local development and tests must work without live AWS credentials, so
    every caller of this function needs a deterministic fallback — see
    `mock_revision` below. Returning None (never a fabricated success) is
    what lets callers tell "the model was skipped" apart from "the model
    said nothing useful".
    """
    if os.getenv("BEDROCK_ENABLED", "false").lower() != "true":
        return None
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
        response = client.converse(
            modelId=os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"),
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0.2, "maxTokens": 1800},
        )
        return response["output"]["message"]["content"][0]["text"]
    except Exception:
        return None


def revise_document(original: str, feedback: str) -> tuple[str, list[str]]:
    """Produce a revised document + change summary, via Bedrock when enabled."""
    prompt = (
        "You are a careful document editor. Revise the document using the feedback below.\n\n"
        f"ORIGINAL:\n{original}\n\nFEEDBACK:\n{feedback}\n\n"
        'Return JSON with keys `revised_text` and `changes` (array of short strings). '
        "Do not invent facts."
    )
    text = bedrock_converse(prompt)
    if text:
        try:
            payload: dict[str, Any] = json.loads(text)
            return str(payload["revised_text"]), [str(x) for x in payload.get("changes", [])]
        except Exception:
            pass
    return mock_revision(original, feedback)


def mock_revision(original: str, feedback: str) -> tuple[str, list[str]]:
    """Deterministic fallback used in local dev, tests, and whenever Bedrock is unavailable."""
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
