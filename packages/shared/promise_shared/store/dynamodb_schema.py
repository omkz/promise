from __future__ import annotations

from typing import Any

from .index_keys import USER_OWNED_INDEX

"""Pure table/GSI definitions for the DynamoDB backend -- no boto3 client calls
here, just the dict payloads `infra/deploy_gsi.py` (or a fresh `CreateTable`)
sends to DynamoDB. Kept next to `dynamodb.py` since the two must never drift:
this module is the single source of truth for the index's name and shape,
`dynamodb.py` is the single source of truth for the keys written into it
(`index_keys.user_owned_index_keys`).
"""

GSI_NAME = USER_OWNED_INDEX


def gsi_definition() -> dict[str, Any]:
    """The `UserOwnedIndex` GSI: partition key `GSI1PK` (`WORKSPACE#<ws>#USER#<user>`),
    sort key `GSI1SK` (`<ENTITY>#<created_at>#<id>`, see index_keys.py).

    Projection is `INCLUDE` with only `data` as a non-key attribute -- not
    `KEYS_ONLY` (the application needs the full item body immediately on every
    list call; `KEYS_ONLY` would mean a second `GetItem` per row, which is
    worse than the workspace-scan this index replaces) and not `ALL` (nothing
    reads any of this table's other top-level attributes -- `entity`/`id`
    off the base table's projected key attributes are enough, and `data`
    holds the whole domain row already; see `DynamoEntityStore._decode`).
    `PK`/`SK` are always projected automatically (DynamoDB projects the base
    table's own key into every index), so `_decode` never needs them here.
    """
    return {
        "IndexName": GSI_NAME,
        "KeySchema": [
            {"AttributeName": "GSI1PK", "KeyType": "HASH"},
            {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "INCLUDE", "NonKeyAttributes": ["data"]},
    }


def table_definition(table_name: str = "PROMISE") -> dict[str, Any]:
    """`CreateTable` payload for a fresh deployment (base table + the GSI together).
    `PAY_PER_REQUEST` (on-demand) matches the rest of this project's "no capacity
    planning to do in local/early-stage deployments" stance -- see infra/README.md."""
    return {
        "TableName": table_name,
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "GlobalSecondaryIndexes": [gsi_definition()],
    }


def add_gsi_update(table_name: str = "PROMISE") -> dict[str, Any]:
    """`UpdateTable` payload for adding the GSI to an *existing* table online --
    DynamoDB backfills a new GSI in place; the table is never recreated and stays
    fully available for reads/writes throughout (see infra/README.md's migration
    note). Only declares the two new attributes the index needs; every other
    attribute definition on the table is untouched."""
    return {
        "TableName": table_name,
        "AttributeDefinitions": [
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexUpdates": [{"Create": gsi_definition()}],
    }
