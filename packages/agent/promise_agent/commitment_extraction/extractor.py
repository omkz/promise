from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from promise_domain.enums import Priority

from .provider import BedrockCommitmentExtractionProvider, CommitmentExtractionProvider, MockCommitmentExtractionProvider
from .schema import CommitmentExtraction, RawCommitmentExtraction
from .temporal import resolve_due_at

logger = logging.getLogger("promise.commitment_extraction")


def default_provider() -> CommitmentExtractionProvider:
    """`BEDROCK_ENABLED=true` -> real Bedrock provider. Otherwise (every local
    dev run and CI job) -> the deterministic mock. Never mandatory for the app
    to boot: `promise_api`/`promise_mcp` never import Bedrock/boto3 directly."""
    if os.getenv("BEDROCK_ENABLED", "false").lower() == "true":
        return BedrockCommitmentExtractionProvider()
    return MockCommitmentExtractionProvider()


class CommitmentExtractor:
    """Turns natural-language text into a validated `CommitmentExtraction`.

    Pure transformation: no DynamoDB, no FastAPI, no MCP, no mutation of any
    `Commitment` row. `promise_agent.steps.extraction` is the only caller that
    persists anything, and it does so from the object this returns.
    """

    def __init__(self, provider: CommitmentExtractionProvider | None = None) -> None:
        self._configured_provider = provider

    def extract(self, text: str, *, now: datetime | None = None, timezone: str | None = None) -> CommitmentExtraction:
        tz_name = timezone or os.getenv("PROMISE_TIMEZONE", "Asia/Jakarta")
        reference = now or datetime.now(ZoneInfo(tz_name))
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=ZoneInfo(tz_name))

        clean = text.strip()
        provider = self._configured_provider or default_provider()
        raw = self._run_provider(provider, clean)

        due_at = resolve_due_at(raw.temporal_expression, now=reference)

        return CommitmentExtraction(
            is_commitment=raw.is_commitment,
            category=raw.category,
            action=raw.action,
            contact_name=raw.contact_name,
            due_at=due_at,
            priority=raw.priority_hint or Priority.MEDIUM,
            confidence=raw.confidence,
            source_excerpt=clean,
            reasoning=raw.reasoning,
            provider_name=provider.provider_name,
            model_id=getattr(provider, "model_id", None),
        )

    def _run_provider(self, provider: CommitmentExtractionProvider, text: str) -> RawCommitmentExtraction:
        """Calls the provider and lets any `ExtractionProviderError` propagate.

        There is deliberately no silent fallback to the mock provider here: a
        provider is either the mock (chosen by `default_provider()` whenever
        `BEDROCK_ENABLED` is false, and which never fails) or an explicitly
        configured real provider (Bedrock) whose failures must surface as a
        controlled, classified error — never as a quietly-different result.
        """
        started = time.monotonic()
        raw: RawCommitmentExtraction | None = None
        try:
            raw = provider.extract(text)
            return raw
        finally:
            duration_ms = (time.monotonic() - started) * 1000
            # Never log raw user content here — only the metadata needed to debug/observe extraction.
            logger.info(
                "commitment_extraction provider=%s model=%s duration_ms=%.1f confidence=%s success=%s",
                provider.provider_name, getattr(provider, "model_id", None), duration_ms,
                getattr(raw, "confidence", None), raw is not None,
            )
