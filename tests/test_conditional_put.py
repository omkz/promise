from __future__ import annotations

import pytest
from promise_domain.enums import ActionStatus
from promise_domain.models import Action
from promise_shared.errors import ConflictError
from promise_shared.ids import new_id
from promise_shared.store.dynamodb import DynamoEntityStore
from promise_shared.store.local_json import LocalJsonEntityStore

"""Coverage for the atomic compare-and-set primitive Issue 1 (approval state machine)
and Issue 2 (duplicate action execution) are both built on: `EntityStore.put(...,
expected_status=...)` and `Repository.update(..., expected_status=...)`. Proven once here
at the store/repository level, directly, rather than only indirectly through the
higher-level approval/execution tests -- this is the primitive those tests all trust."""


# ---- LocalJsonEntityStore -----------------------------------------------------------------------

def test_local_store_put_succeeds_when_expected_status_matches(tmp_path):
    store = LocalJsonEntityStore(str(tmp_path))
    store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "approved"})
    store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "executing"}, expected_status="approved")
    assert store.get("action", "ws_1", "act_1")["status"] == "executing"


def test_local_store_put_raises_conflict_when_expected_status_does_not_match(tmp_path):
    store = LocalJsonEntityStore(str(tmp_path))
    store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "executing"})
    with pytest.raises(ConflictError):
        store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "executing"}, expected_status="approved")
    # the row was never overwritten by the failed conditional write
    assert store.get("action", "ws_1", "act_1")["status"] == "executing"


def test_local_store_put_raises_conflict_for_missing_row(tmp_path):
    store = LocalJsonEntityStore(str(tmp_path))
    with pytest.raises(ConflictError):
        store.put("action", {"id": "never_written", "workspace_id": "ws_1", "status": "executing"}, expected_status="approved")


# ---- DynamoEntityStore (hand-written fake boto3 Table, no live AWS) ------------------------------

class _FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict] = {}

    def put_item(self, Item, ConditionExpression=None):
        from boto3.dynamodb.conditions import ConditionBase

        key = (Item["PK"], Item["SK"])
        if ConditionExpression is not None:
            assert isinstance(ConditionExpression, ConditionBase)
            existing = self.items.get(key)
            current_status = existing.get("status") if existing else None
            # Emulate DynamoDB's own ConditionExpression evaluation just enough for
            # this test double: `Attr("status").eq(expected)`.
            expected = ConditionExpression._values[1]
            if current_status != expected:
                from botocore.exceptions import ClientError

                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}}, "PutItem")
        self.items[key] = Item

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}


def _dynamo_store(monkeypatch) -> DynamoEntityStore:
    import boto3

    table = _FakeTable()
    monkeypatch.setattr(boto3, "resource", lambda *a, **kw: type("R", (), {"Table": lambda self, name: table})())
    return DynamoEntityStore(table_name="PROMISE", region="us-east-1")


def test_dynamo_store_put_succeeds_when_expected_status_matches(monkeypatch):
    store = _dynamo_store(monkeypatch)
    store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "approved"})
    store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "executing"}, expected_status="approved")
    assert store.get("action", "ws_1", "act_1")["status"] == "executing"


def test_dynamo_store_put_raises_conflict_on_conditional_check_failure(monkeypatch):
    store = _dynamo_store(monkeypatch)
    store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "executing"})
    with pytest.raises(ConflictError):
        store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "executing"}, expected_status="approved")


def test_dynamo_store_writes_status_as_a_top_level_attribute_for_the_condition_to_use(monkeypatch):
    import boto3

    table = _FakeTable()
    monkeypatch.setattr(boto3, "resource", lambda *a, **kw: type("R", (), {"Table": lambda self, name: table})())
    store = DynamoEntityStore(table_name="PROMISE", region="us-east-1")
    store.put("action", {"id": "act_1", "workspace_id": "ws_1", "status": "approved"})
    raw = table.items[("WORKSPACE#ws_1", "ACTION#act_1")]
    assert raw["status"] == "approved"


# ---- Repository.update(..., expected_status=...) ------------------------------------------------

def test_repository_update_with_expected_status_succeeds_on_matching_state(ctx):
    action = Action(
        id=new_id("act"), workspace_id=ctx.default_workspace_id, type="send_message",
        status=ActionStatus.APPROVED, idempotency_key="key_1",
    )
    ctx.repos.actions.save(action)

    updated = ctx.repos.actions.update(
        ctx.default_workspace_id, action.id, lambda a: setattr(a, "status", ActionStatus.EXECUTING),
        expected_status=ActionStatus.APPROVED,
    )
    assert updated.status == ActionStatus.EXECUTING


def test_repository_update_with_expected_status_raises_conflict_on_stale_state(ctx):
    action = Action(
        id=new_id("act"), workspace_id=ctx.default_workspace_id, type="send_message",
        status=ActionStatus.EXECUTING, idempotency_key="key_1",
    )
    ctx.repos.actions.save(action)

    with pytest.raises(ConflictError):
        ctx.repos.actions.update(
            ctx.default_workspace_id, action.id, lambda a: setattr(a, "status", ActionStatus.EXECUTING),
            expected_status=ActionStatus.APPROVED,
        )
    # never mutated by the failed conditional update
    assert ctx.repos.actions.require(ctx.default_workspace_id, action.id).status == ActionStatus.EXECUTING
