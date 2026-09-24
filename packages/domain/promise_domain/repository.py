from __future__ import annotations

from typing import Any, Callable, Generic, TypeVar

from promise_shared.clock import iso_now
from promise_shared.errors import ConflictError, NotFoundError
from promise_shared.store import EntityStore
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Repository(Generic[T]):
    """Workspace-scoped CRUD over a typed domain model, backed by an EntityStore.

    This is the seam that keeps the domain layer ignorant of DynamoDB vs.
    local JSON: every domain service depends only on `Repository[T]`.
    """

    def __init__(self, store: EntityStore, entity: str, model: type[T]) -> None:
        self._store = store
        self._entity = entity
        self._model = model

    def save(self, item: T) -> T:
        self._store.put(self._entity, item.model_dump(mode="json"))
        return item

    def get(self, workspace_id: str, item_id: str) -> T | None:
        row = self._store.get(self._entity, workspace_id, item_id)
        return self._model.model_validate(row) if row else None

    def require(self, workspace_id: str, item_id: str) -> T:
        item = self.get(workspace_id, item_id)
        if item is None:
            raise NotFoundError(self._entity, item_id)
        return item

    def list(self, workspace_id: str, *, include_deleted: bool = False, **filters: Any) -> list[T]:
        """`filters` are exact-match field filters (e.g. `user_id="usr_123"`),
        applied after the workspace-scoped store query.

        The `EntityStore` backends (local JSON, DynamoDB single-table) only
        support querying by workspace partition key — there's no secondary
        index to push a `user_id` filter down to, and adding one (e.g. a
        DynamoDB GSI) is a real infrastructure change, not something to sneak
        in here. This is still the *correct* seam for it, though: the
        repository is the one place every caller goes through, so an
        unauthorized row is never handed back to any of them — a future GSI
        would replace the Python-side filter below without any caller
        changing. Never a substitute for a transport layer (e.g. FastAPI
        router) doing its own after-the-fact filtering.
        """
        rows = self._store.query(self._entity, workspace_id)
        items = [self._model.model_validate(r) for r in rows]
        if not include_deleted and "deleted_at" in self._model.model_fields:
            items = [i for i in items if getattr(i, "deleted_at", None) is None]
        for field, value in filters.items():
            items = [i for i in items if getattr(i, field, None) == value]
        return items

    def update(
        self,
        workspace_id: str,
        item_id: str,
        mutate: Callable[[T], None],
        *,
        expected_version: int | None = None,
    ) -> T:
        item = self.require(workspace_id, item_id)
        if expected_version is not None:
            current_version = getattr(item, "version", None)
            if current_version is not None and current_version != expected_version:
                raise ConflictError(f"{self._entity} '{item_id}' version mismatch")
        mutate(item)
        if "version" in self._model.model_fields:
            setattr(item, "version", getattr(item, "version") + 1)
        if "updated_at" in self._model.model_fields:
            setattr(item, "updated_at", iso_now())
        return self.save(item)

    def soft_delete(self, workspace_id: str, item_id: str) -> None:
        if "deleted_at" not in self._model.model_fields:
            raise ValueError(f"{self._entity} does not support soft delete")
        self.update(workspace_id, item_id, lambda item: setattr(item, "deleted_at", iso_now()))
