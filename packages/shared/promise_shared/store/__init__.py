from __future__ import annotations

import os
from typing import Protocol

from .dynamodb import DynamoEntityStore
from .local_json import LocalJsonEntityStore


class EntityStore(Protocol):
    """Generic, workspace-scoped key/value entity store.

    Every row is a plain dict that must contain at least: id, workspace_id.
    Repositories in promise_domain build typed models on top of this; the
    store itself knows nothing about domain semantics, only "entity kind +
    workspace_id + id".
    """

    def put(self, entity: str, item: dict) -> dict: ...

    def get(self, entity: str, workspace_id: str, item_id: str) -> dict | None: ...

    def query(self, entity: str, workspace_id: str) -> list[dict]: ...

    def query_all(self, entity: str) -> list[dict]:
        """Admin/maintenance use only (e.g. cross-workspace jobs). Never expose to tenants."""
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


__all__ = ["EntityStore", "build_store", "DynamoEntityStore", "LocalJsonEntityStore"]
