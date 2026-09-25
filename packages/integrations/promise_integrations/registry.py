from __future__ import annotations

from typing import Any, Callable

from promise_domain.enums import IntegrationStatus
from promise_domain.models import IntegrationAccount
from promise_domain.repository import Repository

from .base import IntegrationProvider


class IntegrationRegistry:
    """Resolves which provider backs a given provider name.

    `get()` resolves a single, workspace-wide, always-available `IntegrationProvider`
    by name -- today only "local" is registered this way, since it needs no
    per-account credentials.

    `resolve_for_user()` is the separate seam for a provider that is
    inherently per-person, not per-workspace (Gmail: each connected
    `IntegrationAccount` is one person's mailbox; Google Calendar: same). It
    looks up that user's own connected account and hands back a *fresh*
    provider instance bound to it -- never a shared singleton, and never a
    fallback to the "local"/default provider (a caller that gets `None` back
    decides for itself what "not connected" means: context retrieval skips
    that provider as a search source; `execute_action` uses the local
    provider instead for message sends). Deliberately untyped beyond `Any`
    for this path -- it's used for both `IntegrationProvider` (Gmail) and
    `CalendarProvider` (Google Calendar) factories, which have no shared
    method shape; the registry itself only ever needs `provider_name` plus
    whatever `IntegrationAccount` ownership check gates access to it, never
    the specific interface a resolved provider satisfies.
    """

    def __init__(
        self, default: IntegrationProvider, *,
        integration_accounts: Repository[IntegrationAccount] | None = None,
        provider_factories: dict[str, Callable[[IntegrationAccount], Any]] | None = None,
    ) -> None:
        self._providers: dict[str, IntegrationProvider] = {default.provider_name: default}
        self._default_name = default.provider_name
        self._integration_accounts = integration_accounts
        self._provider_factories = provider_factories or {}

    def register(self, provider: IntegrationProvider) -> None:
        self._providers[provider.provider_name] = provider

    def register_factory(self, provider_name: str, factory: Callable[[IntegrationAccount], Any]) -> None:
        """Register (or override, e.g. in tests) the factory `resolve_for_user`
        uses for `provider_name`. Requires `integration_accounts` to have been
        supplied at construction -- there's nothing to resolve an account
        against otherwise."""
        if self._integration_accounts is None:
            raise ValueError("IntegrationRegistry was built without integration_accounts; resolve_for_user cannot work")
        self._provider_factories[provider_name] = factory

    def get(self, provider_name: str | None = None) -> IntegrationProvider:
        return self._providers[provider_name or self._default_name]

    def resolve_for_user(self, workspace_id: str, user_id: str, *, provider_name: str) -> Any | None:
        """Return a provider bound to this user's own connected `provider_name`
        account, or `None` if they have none connected (never raises for "not
        connected" -- that's an ordinary, expected state here, not an error;
        `IntegrationNotConnected` is for a resolved provider being called
        without valid credentials, a different case). Never trusts a caller-
        supplied account id: the account is looked up strictly by
        `workspace_id` + `user_id` (the authenticated principal), the same
        ownership seam every other user-owned list/lookup in this codebase
        uses (see `Repository.list_user_owned`)."""
        factory = self._provider_factories.get(provider_name)
        if factory is None or self._integration_accounts is None:
            return None
        accounts = self._integration_accounts.list_user_owned(
            workspace_id, user_id, provider=provider_name, status=IntegrationStatus.CONNECTED
        )
        if not accounts:
            return None
        return factory(accounts[0])
