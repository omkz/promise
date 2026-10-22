from __future__ import annotations

from typing import TYPE_CHECKING

from promise_shared.errors import StrandsConfigurationError

from .config import StrandsConfig, validate_strands_config

if TYPE_CHECKING:
    from strands.models.model import Model


class StrandsModelFactory:
    """Isolated model factory for Strands Agents SDK.

    Keeps model provider selection and instantiation separate from agent orchestration
    and business logic.
    """

    @staticmethod
    def create_model(config: StrandsConfig) -> Model:
        validate_strands_config(config)

        try:
            if config.provider == "google":
                from strands.models.gemini import GeminiModel

                return GeminiModel(
                    client_args={"api_key": config.gemini_api_key},
                    model_id=config.model_id,
                )

            if config.provider == "openai":
                from strands.models.openai import OpenAIModel

                return OpenAIModel(
                    client_args={"api_key": config.openai_api_key},
                    model_id=config.model_id,
                )

            if config.provider == "anthropic":
                from strands.models.anthropic import AnthropicModel

                return AnthropicModel(
                    client_args={"api_key": config.anthropic_api_key},
                    model_id=config.model_id,
                )

            if config.provider == "bedrock":
                from strands.models.bedrock import BedrockModel

                return BedrockModel(
                    model_id=config.model_id,
                    region_name=config.aws_region,
                )

            raise StrandsConfigurationError(f"Unsupported provider: '{config.provider}'")
        except StrandsConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StrandsConfigurationError(
                f"Failed to initialize model provider '{config.provider}': {exc}"
            ) from exc
