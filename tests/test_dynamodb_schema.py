from __future__ import annotations

from promise_shared.store.dynamodb_schema import GSI_NAME, add_gsi_update, gsi_definition, table_definition
from promise_shared.store.index_keys import USER_OWNED_INDEX

"""Infrastructure tests for the `UserOwnedIndex` GSI definition
(`promise_shared.store.dynamodb_schema`) -- pure dict-payload assertions, no
AWS credentials or network access needed. Locks in the exact shape
`infra/deploy_gsi.py` (or a fresh CreateTable) sends to DynamoDB."""


def test_gsi_name_matches_the_key_scheme_the_store_implementation_uses():
    assert GSI_NAME == USER_OWNED_INDEX


def test_gsi_key_schema_is_partition_and_sort():
    gsi = gsi_definition()
    assert gsi["KeySchema"] == [
        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
    ]


def test_gsi_projection_includes_only_the_item_body_not_every_attribute():
    gsi = gsi_definition()
    assert gsi["Projection"]["ProjectionType"] == "INCLUDE"
    assert gsi["Projection"]["NonKeyAttributes"] == ["data"]


def test_table_definition_creates_the_gsi_alongside_the_base_table():
    table = table_definition()
    assert table["KeySchema"] == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    assert table["BillingMode"] == "PAY_PER_REQUEST"
    assert any(g["IndexName"] == GSI_NAME for g in table["GlobalSecondaryIndexes"])
    attr_names = {a["AttributeName"] for a in table["AttributeDefinitions"]}
    assert {"PK", "SK", "GSI1PK", "GSI1SK"} <= attr_names


def test_add_gsi_update_is_additive_not_destructive():
    """The existing-table migration path: only declares the GSI's own new
    attributes and a Create action -- never touches the base table's own
    KeySchema/BillingMode, so it can never be mistaken for a recreate."""
    update = add_gsi_update("PROMISE")
    assert update["TableName"] == "PROMISE"
    assert update["GlobalSecondaryIndexUpdates"] == [{"Create": gsi_definition()}]
    assert "KeySchema" not in update
    assert "BillingMode" not in update
    attr_names = {a["AttributeName"] for a in update["AttributeDefinitions"]}
    assert attr_names == {"GSI1PK", "GSI1SK"}


def test_table_name_is_configurable_for_both_payloads():
    assert table_definition("OtherTable")["TableName"] == "OtherTable"
    assert add_gsi_update("OtherTable")["TableName"] == "OtherTable"
