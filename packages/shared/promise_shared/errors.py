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


class VerifiedContactRequiredError(PromiseError):
    """Raised when an external send action needs a contact with a verified email
    on file, and none is available. PROMISE never invents a contact email
    (e.g. from a first name) to work around this."""

    def __init__(self, *, contact_id: str | None = None, contact_name: str | None = None) -> None:
        who = f"'{contact_name}'" if contact_name else (contact_id or "the recipient")
        super().__init__(f"a verified email is required for {who} before PROMISE can send this — none is on file")
        self.contact_id = contact_id
        self.contact_name = contact_name
