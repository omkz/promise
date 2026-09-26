from __future__ import annotations

import logging
import time
from typing import Any, Callable, Generic, TypeVar

from promise_shared.clock import iso_now
from promise_shared.errors import ConflictError, NotFoundError
from promise_shared.store import EntityStore
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


def _status_value(status: Any) -> Any:
    """Normalize a status for comparison -- an enum member's own `.value` (a
    plain string, matching what `model_dump(mode="json")` and every store
    backend's `data`/`status` attribute already serialize to) or the value
    itself if it's already plain."""
    return status.value if hasattr(status, "value") else status


class Repository(Generic[T]):
    """Workspace-scoped CRUD over a typed domain model, backed by an EntityStore.

    This is the seam that keeps the domain layer ignorant of DynamoDB vs.
    local JSON: every domain service depends only on `Repository[T]`.
    """

    def __init__(self, store: EntityStore, entity: str, model: type[T], *, user_index: str | None = None) -> None:
        self._store = store
        self._entity = entity
        self._model = model
        # Set only for entities registered in `promise_shared.store.index_keys.
        # USER_OWNED_ENTITIES` (see repos.py) -- gates `list_user_owned` below.
        self._user_index = user_index

    def save(self, item: T, *, expected_status: Any | None = None) -> T:
        self._store.put(
            self._entity, item.model_dump(mode="json"),
            expected_status=_status_value(expected_status) if expected_status is not None else None,
        )
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

    def list_user_owned(
        self, workspace_id: str, user_id: str, *, include_deleted: bool = False, limit: int | None = None, **filters: Any
    ) -> list[T]:
        """The explicit, indexed path for a user-owned entity's list access pattern
        (workspace_id + user_id) -- only available on repositories constructed
        with `user_index` set (`Commitment`, `IntegrationAccount`; see repos.py).

        Unlike `list()`, this never reads every row of this entity in the whole
        workspace: it queries `EntityStore.query_index` against the shared
        `UserOwnedIndex` GSI (`GSI1PK = WORKSPACE#<workspace_id>#USER#<user_id>`,
        narrowed to this entity type via a `sort_key_prefix`), which DynamoDB
        serves as a Query, never a Scan. `filters` are still applied in Python
        afterward, same as `list()` -- but only over the caller's own, already
        narrow, result set, not the whole workspace partition. `limit`, when
        given, is pushed down to the Query itself (DynamoDB's own `Limit`, capping
        items *read* per page) -- not a substitute for `filters`/`include_deleted`,
        which still run after, same as real DynamoDB's Limit-then-client-filter
        semantics: the final result can be shorter than `limit`.
        """
        if self._user_index is None:
            raise ValueError(f"'{self._entity}' has no user-owned index configured (see repos.py)")
        start = time.perf_counter()
        rows = self._store.query_index(
            self._user_index, f"WORKSPACE#{workspace_id}#USER#{user_id}", sort_key_prefix=f"{self._entity.upper()}#", limit=limit
        )
        items = [self._model.model_validate(r) for r in rows]
        if not include_deleted and "deleted_at" in self._model.model_fields:
            items = [i for i in items if getattr(i, "deleted_at", None) is None]
        for field, value in filters.items():
            items = [i for i in items if getattr(i, field, None) == value]
        logger.info(
            "indexed_query entity=%s index=%s access_pattern=user_owned result_count=%d duration_ms=%.2f",
            self._entity, self._user_index, len(items), (time.perf_counter() - start) * 1000,
        )
        return items

    def update(
        self,
        workspace_id: str,
        item_id: str,
        mutate: Callable[[T], None],
        *,
        expected_version: int | None = None,
        expected_status: Any | None = None,
    ) -> T:
        """`expected_status`, when given, makes this a real atomic state-machine
        transition rather than a plain read-then-write: the item must currently
        have this status (checked here, for a clear, immediate error on the
        common non-racing case), *and* the underlying write is conditioned on
        that same status still holding at write time (`EntityStore.put`'s own
        `ConditionExpression`/lock) -- so a second caller racing to make the same
        transition concurrently can never both succeed, even though both may
        have read the pre-transition status. Raises `ConflictError` either way
        (fast pre-check or lost race) -- callers that need a different, more
        specific exception (e.g. `DuplicateActionError`) catch `ConflictError`
        and re-raise it themselves; this method stays domain-model-agnostic.

        `expected_version` (Commitment's own, older optimistic-concurrency
        convention) is unrelated and unchanged -- still an app-level-only
        pre-check, not pushed down to the store."""
        item = self.require(workspace_id, item_id)
        if expected_version is not None:
            current_version = getattr(item, "version", None)
            if current_version is not None and current_version != expected_version:
                raise ConflictError(f"{self._entity} '{item_id}' version mismatch")
        current_status = getattr(item, "status", None)
        if expected_status is not None and _status_value(current_status) != _status_value(expected_status):
            raise ConflictError(
                f"{self._entity} '{item_id}' is not in the expected state "
                f"(expected {_status_value(expected_status)!r}, found {_status_value(current_status)!r})"
            )
        mutate(item)
        if "version" in self._model.model_fields:
            setattr(item, "version", getattr(item, "version") + 1)
        if "updated_at" in self._model.model_fields:
            setattr(item, "updated_at", iso_now())
        return self.save(item, expected_status=current_status if expected_status is not None else None)

    def soft_delete(self, workspace_id: str, item_id: str) -> None:
        if "deleted_at" not in self._model.model_fields:
            raise ValueError(f"{self._entity} does not support soft delete")
        self.update(workspace_id, item_id, lambda item: setattr(item, "deleted_at", iso_now()))
