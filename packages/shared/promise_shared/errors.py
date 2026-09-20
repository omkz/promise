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
