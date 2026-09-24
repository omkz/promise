from __future__ import annotations

import json
from typing import Any

from .index_keys import USER_OWNED_ENTITIES, user_owned_index_keys


class DynamoEntityStore:
    """Single-table DynamoDB adapter.

    Table layout: PK = "WORKSPACE#<workspace_id>", SK = "<ENTITY>#<id>".
    A GSI (entity-index: PK=entity, SK=id) is recommended for query_all /
    cross-workspace maintenance jobs; query_all here falls back to a scan
    when that index isn't present, which is fine for low-volume admin use
    but should not sit on any tenant-facing request path.

    A second GSI (`index_keys.USER_OWNED_INDEX`, "GSI1PK"/"GSI1SK") backs
    `query_index` for the small set of user-owned entities in
    `index_keys.USER_OWNED_ENTITIES` -- see that module's docstring. Its
    key attributes are only written for rows of those entities; every other
    entity's `put_item` call is unaffected. See `infra/README.md` and
    `promise_shared.store.dynamodb_schema` for the index's table definition.

    Item bodies are stored as a single JSON-encoded "data" attribute rather
    than exploded top-level attributes, so the domain layer's shape can
    evolve without a table migration.
    """

    def __init__(self, table_name: str, region: str) -> None:
        import boto3

        self._boto3 = boto3
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    @staticmethod
    def _keys(entity: str, workspace_id: str, item_id: str) -> tuple[str, str]:
        return f"WORKSPACE#{workspace_id}", f"{entity.upper()}#{item_id}"

    def put(self, entity: str, item: dict) -> dict:
        if "id" not in item or "workspace_id" not in item:
            raise ValueError("entity rows must include 'id' and 'workspace_id'")
        pk, sk = self._keys(entity, item["workspace_id"], item["id"])
        row: dict[str, Any] = {"PK": pk, "SK": sk, "entity": entity, "id": item["id"], "data": json.dumps(item)}
        if entity in USER_OWNED_ENTITIES and item.get("user_id"):
            gsi_pk, gsi_sk = user_owned_index_keys(entity, item["workspace_id"], item["user_id"], item.get("created_at", ""), item["id"])
            row["GSI1PK"], row["GSI1SK"] = gsi_pk, gsi_sk
        self.table.put_item(Item=row)
        return item

    def get(self, entity: str, workspace_id: str, item_id: str) -> dict | None:
        pk, sk = self._keys(entity, workspace_id, item_id)
        resp = self.table.get_item(Key={"PK": pk, "SK": sk})
        item = resp.get("Item")
        return self._decode(item) if item else None

    def query(self, entity: str, workspace_id: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"WORKSPACE#{workspace_id}") & Key("SK").begins_with(f"{entity.upper()}#")
        )
        return [self._decode(x) for x in resp.get("Items", [])]

    def query_index(self, index_name: str, partition_key: str, *, sort_key_prefix: str | None = None) -> list[dict]:
        """Query a named GSI by partition key, optionally narrowed by a sort-key
        `begins_with` prefix. Always a DynamoDB Query against `IndexName`, never
        a Scan -- this is the method the ownership-filtered list access patterns
        (`Repository.list_user_owned`) go through instead of `query` + Python
        filtering."""
        from boto3.dynamodb.conditions import Key

        condition = Key("GSI1PK").eq(partition_key)
        if sort_key_prefix:
            condition = condition & Key("GSI1SK").begins_with(sort_key_prefix)
        resp = self.table.query(IndexName=index_name, KeyConditionExpression=condition)
        return [self._decode(x) for x in resp.get("Items", [])]

    def query_all(self, entity: str) -> list[dict]:
        scan = self.table.scan(FilterExpression="entity = :e", ExpressionAttributeValues={":e": entity})
        return [self._decode(x) for x in scan.get("Items", [])]

    def delete(self, entity: str, workspace_id: str, item_id: str) -> None:
        pk, sk = self._keys(entity, workspace_id, item_id)
        self.table.delete_item(Key={"PK": pk, "SK": sk})

    @staticmethod
    def _decode(item: dict[str, Any]) -> dict[str, Any]:
        if "data" in item:
            return json.loads(item["data"])
        return item
