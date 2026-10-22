from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from promise_shared.errors import StrandsConfigurationError

SUPPORTED_PROVIDERS: Final[tuple[str, ...]] = ("google", "openai", "anthropic", "bedrock", "groq")

DEFAULT_MODEL_IDS: Final[dict[str, str]] = {
    "google": "gemini-2.5-flash",
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-latest",
    "bedrock": "anthropic.claude-sonnet-4-20250514-v1:0",
    "groq": "openai/gpt-oss-120b",
}


@dataclass(frozen=True)
class StrandsConfig:
    enabled: bool = False
    provider: str = "google"
    model_id: str = "gemini-2.5-flash"
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    aws_region: str = "us-east-1"
    timeout_seconds: float = 30.0


def load_strands_config() -> StrandsConfig:
    """Load and return Strands configuration from environment variables."""
    enabled_str = os.getenv("STRANDS_ENABLED", "false").lower()
    enabled = enabled_str in ("true", "1", "yes")

    provider = os.getenv("STRANDS_MODEL_PROVIDER", "google").strip().lower()
    default_model = DEFAULT_MODEL_IDS.get(provider, "gemini-2.5-flash")
    model_id = os.getenv("STRANDS_MODEL_ID", "").strip() or default_model

    gemini_key = os.getenv("GEMINI_API_KEY") or None
    openai_key = os.getenv("OPENAI_API_KEY") or None
    anthropic_key = os.getenv("ANTHROPIC_API_KEY") or None
    groq_key = os.getenv("GROQ_API_KEY") or None
    aws_region = os.getenv("AWS_REGION", "us-east-1")

    timeout_raw = os.getenv("STRANDS_TIMEOUT_SECONDS", "30")
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        timeout_seconds = 30.0

    return StrandsConfig(
        enabled=enabled,
        provider=provider,
        model_id=model_id,
        gemini_api_key=gemini_key,
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        groq_api_key=groq_key,
        aws_region=aws_region,
        timeout_seconds=timeout_seconds,
    )


def validate_strands_config(config: StrandsConfig) -> None:
    """Validate that the configured provider and its required credentials are present."""
    if config.provider not in SUPPORTED_PROVIDERS:
        supported_str = ", ".join(SUPPORTED_PROVIDERS)
        raise StrandsConfigurationError(
            f"Unsupported model provider '{config.provider}'. Supported providers: {supported_str}"
        )

    if config.provider == "google" and not config.gemini_api_key:
        raise StrandsConfigurationError(
            "GEMINI_API_KEY is required when STRANDS_MODEL_PROVIDER='google'. Provide a valid API key."
        )

    if config.provider == "openai" and not config.openai_api_key:
        raise StrandsConfigurationError(
            "OPENAI_API_KEY is required when STRANDS_MODEL_PROVIDER='openai'. Provide a valid API key."
        )

    if config.provider == "anthropic" and not config.anthropic_api_key:
        raise StrandsConfigurationError(
            "ANTHROPIC_API_KEY is required when STRANDS_MODEL_PROVIDER='anthropic'. Provide a valid API key."
        )

    if config.provider == "groq" and not config.groq_api_key:
        raise StrandsConfigurationError(
            "GROQ_API_KEY is required when STRANDS_MODEL_PROVIDER='groq'. Provide a valid API key."
        )
