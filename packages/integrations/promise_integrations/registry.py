from __future__ import annotations

from .base import IntegrationProvider


class IntegrationRegistry:
    """Resolves which IntegrationProvider backs a given provider name.

    Today only "local" is implemented. A production deployment would
    register "gmail", "google_drive", "slack", etc. here, each resolved
    per-workspace based on the workspace's connected IntegrationAccount —
    the agent and application services stay untouched either way.
    """

    def __init__(self, default: IntegrationProvider) -> None:
        self._providers: dict[str, IntegrationProvider] = {default.provider_name: default}
        self._default_name = default.provider_name

    def register(self, provider: IntegrationProvider) -> None:
        self._providers[provider.provider_name] = provider

    def get(self, provider_name: str | None = None) -> IntegrationProvider:
        return self._providers[provider_name or self._default_name]
