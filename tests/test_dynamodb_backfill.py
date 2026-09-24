from __future__ import annotations

import json

import pytest
from promise_shared.store.dynamodb import DynamoEntityStore

"""Coverage for `DynamoEntityStore.backfill_user_owned_index` -- the one-time/
rerunnable maintenance job that populates `GSI1PK`/`GSI1SK` on rows written
before the `UserOwnedIndex` GSI existed (DynamoDB's own online GSI backfill
only indexes items that already have the key attributes at creation time, so
older rows are otherwise silently invisible to `query_index` forever). No
live AWS credentials/network required -- `boto3.resource` is monkeypatched to
a stateful fake table, in the same style as tests/test_dynamodb_index.py."""


class _FakeTable:
    def __init__(self, page_size: int | None = None) -> None:
        self._items: dict[tuple[str, str], dict] = {}
        self.scan_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.page_size = page_size

    def put_item(self, Item):
        self._items[(Item["PK"], Item["SK"])] = dict(Item)

    def get_item(self, Key):
        item = self._items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs):
        return {"Items": []}

    def scan(self, FilterExpression=None, ExpressionAttributeValues=None, ExclusiveStartKey=None, **kwargs):
        self.scan_calls.append({"ExclusiveStartKey": ExclusiveStartKey})
        entity = (ExpressionAttributeValues or {}).get(":e")
        items = sorted((v for v in self._items.values() if entity is None or v.get("entity") == entity), key=lambda i: i["SK"])
        if self.page_size is None:
            return {"Items": items}
        start = 0
        if ExclusiveStartKey is not None:
            for i, it in enumerate(items):
                if it["PK"] == ExclusiveStartKey["PK"] and it["SK"] == ExclusiveStartKey["SK"]:
                    start = i + 1
                    break
        page = items[start:start + self.page_size]
        result = {"Items": page}
        if start + self.page_size < len(items):
            result["LastEvaluatedKey"] = {"PK": page[-1]["PK"], "SK": page[-1]["SK"]}
        return result

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.update_calls.append({"Key": Key, "values": dict(ExpressionAttributeValues)})
        item = self._items[(Key["PK"], Key["SK"])]
        item["GSI1PK"] = ExpressionAttributeValues[":pk"]
        item["GSI1SK"] = ExpressionAttributeValues[":sk"]


class _FakeResource:
    def __init__(self, table: _FakeTable) -> None:
        self._table = table

    def Table(self, name):
        return self._table


def _store(monkeypatch, page_size: int | None = None) -> tuple[DynamoEntityStore, _FakeTable]:
    import boto3

    table = _FakeTable(page_size=page_size)
    monkeypatch.setattr(boto3, "resource", lambda *a, **kw: _FakeResource(table))
    return DynamoEntityStore(table_name="PROMISE", region="us-east-1"), table


def _legacy_row(table: _FakeTable, *, entity: str, item_id: str, workspace_id: str, user_id: str | None, created_at: str) -> None:
    """A row as it would look if written before the GSI existed: no GSI1PK/GSI1SK
    at all. Bypasses `DynamoEntityStore.put` (which always writes them now) by
    talking to the fake table directly."""
    body = {"id": item_id, "workspace_id": workspace_id, "created_at": created_at}
    if user_id is not None:
        body["user_id"] = user_id
    table.put_item({
        "PK": f"WORKSPACE#{workspace_id}", "SK": f"{entity.upper()}#{item_id}",
        "entity": entity, "id": item_id, "data": json.dumps(body),
    })


def test_backfill_populates_missing_gsi_keys(monkeypatch):
    store, table = _store(monkeypatch)
    _legacy_row(table, entity="commitment", item_id="com_1", workspace_id="ws_1", user_id="usr_1", created_at="2026-01-01T00:00:00+00:00")

    counts = store.backfill_user_owned_index("commitment")

    assert counts == {"scanned": 1, "updated": 1, "already_correct": 0, "skipped_no_user_id": 0}
    item = table._items[("WORKSPACE#ws_1", "COMMITMENT#com_1")]
    assert item["GSI1PK"] == "WORKSPACE#ws_1#USER#usr_1"
    assert item["GSI1SK"] == "COMMITMENT#2026-01-01T00:00:00+00:00#com_1"


def test_backfill_is_idempotent_on_rerun(monkeypatch):
    store, table = _store(monkeypatch)
    store.put("commitment", {"id": "com_1", "workspace_id": "ws_1", "user_id": "usr_1", "created_at": "2026-01-01T00:00:00+00:00"})

    first = store.backfill_user_owned_index("commitment")
    assert first["updated"] == 0  # store.put already wrote correct keys
    assert first["already_correct"] == 1

    second = store.backfill_user_owned_index("commitment")
    assert second == first
    assert len(table.update_calls) == 0


def test_backfill_only_updates_rows_that_are_actually_missing_or_wrong(monkeypatch):
    store, table = _store(monkeypatch)
    _legacy_row(table, entity="commitment", item_id="com_1", workspace_id="ws_1", user_id="usr_1", created_at="2026-01-01T00:00:00+00:00")
    store.put("commitment", {"id": "com_2", "workspace_id": "ws_1", "user_id": "usr_2", "created_at": "2026-01-02T00:00:00+00:00"})

    counts = store.backfill_user_owned_index("commitment")

    assert counts["scanned"] == 2
    assert counts["updated"] == 1
    assert counts["already_correct"] == 1
    assert len(table.update_calls) == 1
    assert table.update_calls[0]["Key"] == {"PK": "WORKSPACE#ws_1", "SK": "COMMITMENT#com_1"}


def test_backfill_never_writes_attributes_other_than_the_gsi_keys(monkeypatch):
    store, table = _store(monkeypatch)
    _legacy_row(table, entity="commitment", item_id="com_1", workspace_id="ws_1", user_id="usr_1", created_at="2026-01-01T00:00:00+00:00")

    store.backfill_user_owned_index("commitment")

    call = table.update_calls[0]
    assert set(call["values"].keys()) == {":pk", ":sk"}
    # the item body itself is untouched
    item = table._items[("WORKSPACE#ws_1", "COMMITMENT#com_1")]
    assert json.loads(item["data"]) == {"id": "com_1", "workspace_id": "ws_1", "user_id": "usr_1", "created_at": "2026-01-01T00:00:00+00:00"}


def test_backfill_skips_rows_with_no_user_id(monkeypatch):
    store, table = _store(monkeypatch)
    _legacy_row(table, entity="commitment", item_id="com_1", workspace_id="ws_1", user_id=None, created_at="2026-01-01T00:00:00+00:00")

    counts = store.backfill_user_owned_index("commitment")

    assert counts["skipped_no_user_id"] == 1
    assert counts["updated"] == 0
    assert len(table.update_calls) == 0


def test_backfill_rejects_entities_outside_the_user_owned_set(monkeypatch):
    store, _table = _store(monkeypatch)
    with pytest.raises(ValueError):
        store.backfill_user_owned_index("contact")


def test_backfill_pages_through_the_whole_scan(monkeypatch):
    store, table = _store(monkeypatch, page_size=1)
    _legacy_row(table, entity="commitment", item_id="com_1", workspace_id="ws_1", user_id="usr_1", created_at="2026-01-01T00:00:00+00:00")
    _legacy_row(table, entity="commitment", item_id="com_2", workspace_id="ws_1", user_id="usr_2", created_at="2026-01-02T00:00:00+00:00")

    counts = store.backfill_user_owned_index("commitment")

    assert counts["scanned"] == 2
    assert counts["updated"] == 2
    assert len(table.scan_calls) == 2  # one page each
    assert table.scan_calls[1]["ExclusiveStartKey"] is not None
