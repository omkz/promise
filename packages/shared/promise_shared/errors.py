from __future__ import annotations


class PromiseError(Exception):
    """Base class for domain/application errors the API and MCP layers translate."""


class NotFoundError(PromiseError):
    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} '{entity_id}' not found")
        self.entity = entity
        self.entity_id = entity_id


class WorkspaceAccessError(PromiseError):
    """Raised when a caller tries to read/write an object outside its workspace."""

    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} '{entity_id}' is not accessible in this workspace")


class ApprovalRequiredError(PromiseError):
    """Raised when execution is attempted on an action that has not been approved."""

    def __init__(self, action_id: str) -> None:
        super().__init__(f"action '{action_id}' requires approval before execution")


class DuplicateActionError(PromiseError):
    """Raised when an action has already been executed and re-execution is attempted."""

    def __init__(self, action_id: str) -> None:
        super().__init__(f"action '{action_id}' has already been executed")


class ConflictError(PromiseError):
    """Optimistic-concurrency violation: the stored version does not match expectations."""


class ExtractionProviderError(PromiseError):
    """Raised when a configured extraction provider (e.g. Bedrock) fails.

    Deliberately never swallowed into a silent fallback: when a provider is
    explicitly configured (`BEDROCK_ENABLED=true`) and it fails, callers must
    see a controlled error rather than a mock result masquerading as a real
    one. `retryable` tells the caller whether retrying the same request is
    expected to help (throttling, timeouts, transient network errors) versus
    not (bad credentials, malformed request, no valid response).
    """

    def __init__(self, provider_name: str, detail: str, *, retryable: bool = True) -> None:
        super().__init__(f"{provider_name} extraction provider failed: {detail}")
        self.provider_name = provider_name
        self.retryable = retryable


class ContextRetrievalProviderError(PromiseError):
    """Raised when a context-retrieval search provider (documents, messages, or a
    future Gmail/Drive/Slack/etc. provider) fails to fetch candidates.

    Never swallowed into a fabricated/empty-but-successful result: the caller
    (`ContextRetriever`) catches this per-provider, records it, and marks the
    overall retrieval `PARTIAL` rather than `COMPLETE` — see
    `promise_agent.context_retrieval.schema.RetrievalStatus`.
    """

    def __init__(self, provider_name: str, detail: str, *, retryable: bool = True) -> None:
        super().__init__(f"{provider_name} context search provider failed: {detail}")
        self.provider_name = provider_name
        self.retryable = retryable


class LLMProviderError(PromiseError):
    """Raised when a configured LLM provider (e.g. Bedrock, for document revision)
    fails to produce usable output.

    Deliberately never swallowed into a silent fallback: `BEDROCK_ENABLED=true` is
    an explicit choice to require a real model, so a failure there must fail the
    agent run clearly (and, via `retryable`, tell the caller whether retrying is
    expected to help) rather than quietly substitute deterministic mock output.
    `BEDROCK_ENABLED=false` never raises this — the mock path is used directly,
    not as a failure fallback.
    """

    def __init__(self, provider_name: str, detail: str, *, retryable: bool = True) -> None:
        super().__init__(f"{provider_name} LLM provider failed: {detail}")
        self.provider_name = provider_name
        self.retryable = retryable


class VerifiedContactRequiredError(PromiseError):
    """Raised when an external send action needs a contact with a verified email
    on file, and none is available. PROMISE never invents a contact email
    (e.g. from a first name) to work around this."""

    def __init__(self, *, contact_id: str | None = None, contact_name: str | None = None) -> None:
        who = f"'{contact_name}'" if contact_name else (contact_id or "the recipient")
        super().__init__(f"a verified email is required for {who} before PROMISE can send this — none is on file")
        self.contact_id = contact_id
        self.contact_name = contact_name


# ---- identity & authorization (see promise_auth) -----------------------------------------------
#
# Defined here, not in `promise_auth`, so every layer (including `promise_domain`/`promise_app`,
# which must never depend on the auth package itself) can raise/catch them through the same
# `PromiseError` taxonomy `services/api/promise_api/main.py` already maps centrally.


class AuthenticationRequired(PromiseError):
    """No credentials supplied at all: no `Authorization` header in `AUTH_MODE=oidc`, or no
    resolvable identity in `AUTH_MODE=local`. Maps to HTTP 401."""

    def __init__(self, detail: str = "authentication is required") -> None:
        super().__init__(detail)


class InvalidToken(PromiseError):
    """The bearer token failed signature, issuer, audience, or format validation. Maps to
    HTTP 401. Never raised from a JWT whose signature was not actually verified — see
    `promise_auth.oidc.OIDCAuthProvider`."""

    def __init__(self, detail: str = "invalid token") -> None:
        super().__init__(detail)


class TokenExpired(PromiseError):
    """The bearer token's `exp` claim is in the past. Maps to HTTP 401."""

    def __init__(self, detail: str = "token has expired") -> None:
        super().__init__(detail)


class InsufficientScope(PromiseError):
    """The token/principal lacks a scope or permission required for this operation. Maps to
    HTTP 403."""

    def __init__(self, detail: str = "insufficient scope") -> None:
        super().__init__(detail)


class WorkspaceAccessDenied(PromiseError):
    """The authenticated principal has no active membership in the requested workspace — a
    distinct check from resource-level workspace scoping (which 404s; see `NotFoundError`).
    This fires earlier, before any resource lookup, so it never reveals whether a specific
    resource in that workspace exists. Maps to HTTP 403."""

    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"no active membership in workspace '{workspace_id}'")
        self.workspace_id = workspace_id
