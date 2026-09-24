from __future__ import annotations

from promise_shared.store.dynamodb import DynamoEntityStore
from promise_shared.store.index_keys import USER_OWNED_INDEX

"""DynamoDB-adapter-specific coverage for the `UserOwnedIndex` GSI: call shape
only (IndexName used, no Scan, correct GSI attributes written) -- correctness
of the actual filtering/ordering logic is covered against the local JSON
emulation in test_user_owned_index.py (both backends derive keys from the
same `index_keys.user_owned_index_keys`, so this only needs to prove the
DynamoDB adapter wires them in correctly). No live AWS credentials or network
access required: `boto3.resource` is monkeypatched to a hand-written fake
Table, the same style tests/test_llm_provider_errors.py uses for Bedrock."""


class _FakeTable:
    def __init__(self) -> None:
        self.put_items: list[dict] = []
        self.query_calls: list[dict] = []
        self.scan_calls = 0

    def put_item(self, Item):
        self.put_items.append(Item)

    def get_item(self, Key):
        return {}

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return {"Items": []}

    def scan(self, **kwargs):
        self.scan_calls += 1
        return {"Items": []}


class _FakeResource:
    def __init__(self, table: _FakeTable) -> None:
        self._table = table

    def Table(self, name):
        return self._table


def _store(monkeypatch) -> tuple[DynamoEntityStore, _FakeTable]:
    import boto3

    table = _FakeTable()
    monkeypatch.setattr(boto3, "resource", lambda *a, **kw: _FakeResource(table))
    return DynamoEntityStore(table_name="PROMISE", region="us-east-1"), table


def test_put_writes_gsi_attributes_for_a_user_owned_entity(monkeypatch):
    store, table = _store(monkeypatch)
    store.put("commitment", {
        "id": "com_1", "workspace_id": "ws_1", "user_id": "usr_1", "created_at": "2026-01-01T00:00:00+00:00",
    })

    item = table.put_items[-1]
    assert item["GSI1PK"] == "WORKSPACE#ws_1#USER#usr_1"
    assert item["GSI1SK"] == "COMMITMENT#2026-01-01T00:00:00+00:00#com_1"


def test_put_writes_gsi_attributes_for_the_other_user_owned_entity(monkeypatch):
    store, table = _store(monkeypatch)
    store.put("integration_account", {
        "id": "ia_1", "workspace_id": "ws_1", "user_id": "usr_1", "created_at": "2026-01-01T00:00:00+00:00",
    })

    item = table.put_items[-1]
    assert item["GSI1PK"] == "WORKSPACE#ws_1#USER#usr_1"
    assert item["GSI1SK"] == "INTEGRATION_ACCOUNT#2026-01-01T00:00:00+00:00#ia_1"


def test_put_omits_gsi_attributes_for_entities_outside_the_user_owned_set(monkeypatch):
    store, table = _store(monkeypatch)
    store.put("contact", {"id": "con_1", "workspace_id": "ws_1", "name": "Andi"})

    item = table.put_items[-1]
    assert "GSI1PK" not in item
    assert "GSI1SK" not in item


def test_put_omits_gsi_attributes_when_the_row_has_no_user_id(monkeypatch):
    """`AgentRun` has its own `user_id` but is not in USER_OWNED_ENTITIES; more
    importantly, no user-owned-entity row should ever get GSI attributes without
    a user_id to key them on."""
    store, table = _store(monkeypatch)
    store.put("commitment", {"id": "com_1", "workspace_id": "ws_1", "user_id": None})

    item = table.put_items[-1]
    assert "GSI1PK" not in item
    assert "GSI1SK" not in item


def test_query_index_uses_index_name_and_never_a_scan(monkeypatch):
    store, table = _store(monkeypatch)
    store.query_index(USER_OWNED_INDEX, "WORKSPACE#ws_1#USER#usr_1", sort_key_prefix="COMMITMENT#")

    assert len(table.query_calls) == 1
    assert table.query_calls[0]["IndexName"] == USER_OWNED_INDEX
    assert "KeyConditionExpression" in table.query_calls[0]
    assert table.scan_calls == 0


def test_query_index_never_falls_back_to_scan_with_no_sort_key_prefix(monkeypatch):
    store, table = _store(monkeypatch)
    store.query_index(USER_OWNED_INDEX, "WORKSPACE#ws_1#USER#usr_1")

    assert table.scan_calls == 0
    assert table.query_calls[0]["IndexName"] == USER_OWNED_INDEX


def test_plain_workspace_query_never_touches_the_gsi_or_scans(monkeypatch):
    store, table = _store(monkeypatch)
    store.query("commitment", "ws_1")

    assert table.scan_calls == 0
    assert "IndexName" not in table.query_calls[0]


def test_query_index_pushes_limit_down_to_the_query(monkeypatch):
    store, table = _store(monkeypatch)
    store.query_index(USER_OWNED_INDEX, "WORKSPACE#ws_1#USER#usr_1", sort_key_prefix="COMMITMENT#", limit=5)

    assert table.query_calls[0]["Limit"] == 5


def test_query_index_omits_limit_kwarg_when_not_given(monkeypatch):
    store, table = _store(monkeypatch)
    store.query_index(USER_OWNED_INDEX, "WORKSPACE#ws_1#USER#usr_1")

    assert "Limit" not in table.query_calls[0]
