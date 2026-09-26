from __future__ import annotations

import os
from typing import Protocol

from .dynamodb import DynamoEntityStore
from .index_keys import USER_OWNED_ENTITIES, USER_OWNED_INDEX
from .local_json import LocalJsonEntityStore


class EntityStore(Protocol):
    """Generic, workspace-scoped key/value entity store.

    Every row is a plain dict that must contain at least: id, workspace_id.
    Repositories in promise_domain build typed models on top of this; the
    store itself knows nothing about domain semantics, only "entity kind +
    workspace_id + id".
    """

    def put(self, entity: str, item: dict, *, expected_status: str | None = None) -> dict:
        """Write `item`. When `expected_status` is given, this must be an atomic
        compare-and-set: the write only succeeds if the row *currently persisted*
        under this entity/id still has `status == expected_status` -- checked and
        written as one atomic operation at the store level (DynamoDB's own
        `ConditionExpression`; a lock held for the full check-then-write on the
        local backend), never a read-then-write race window. Raises
        `promise_shared.errors.ConflictError` when the condition fails (the row's
        status has already moved on). This is the primitive
        `Repository.update(..., expected_status=...)` is built on -- see that
        method's docstring for why callers should go through it rather than this
        directly."""
        ...

    def get(self, entity: str, workspace_id: str, item_id: str) -> dict | None: ...

    def query(self, entity: str, workspace_id: str) -> list[dict]: ...

    def query_all(self, entity: str) -> list[dict]:
        """Admin/maintenance use only (e.g. cross-workspace jobs). Never expose to tenants."""
        ...

    def query_index(
        self, index_name: str, partition_key: str, *, sort_key_prefix: str | None = None, limit: int | None = None
    ) -> list[dict]:
        """Query a named secondary index by partition key, optionally narrowed by a
        sort-key `begins_with` prefix and/or capped with `limit`. Backs
        `Repository.list_user_owned` -- an implementation must never fall back to
        a full-table Scan here. See `store/index_keys.py` for the one GSI this
        currently supports. No pagination cursor: nothing in this codebase's
        repository layer supports cursor-based pagination yet (see
        `Repository.list`), so there is nothing for one to be consistent with."""
        ...

    def delete(self, entity: str, workspace_id: str, item_id: str) -> None: ...


def build_store() -> EntityStore:
    backend = os.getenv("STORAGE_BACKEND", "local").lower()
    if backend == "dynamodb":
        return DynamoEntityStore(
            table_name=os.getenv("DYNAMODB_TABLE", "PROMISE"),
            region=os.getenv("AWS_REGION", "us-east-1"),
        )
    return LocalJsonEntityStore(os.getenv("LOCAL_DATA_DIR", "./data"))


__all__ = [
    "EntityStore", "build_store", "DynamoEntityStore", "LocalJsonEntityStore",
    "USER_OWNED_INDEX", "USER_OWNED_ENTITIES",
]
