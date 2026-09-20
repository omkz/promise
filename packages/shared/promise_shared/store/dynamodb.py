from __future__ import annotations

import json
from typing import Any


class DynamoEntityStore:
    """Single-table DynamoDB adapter.

    Table layout: PK = "WORKSPACE#<workspace_id>", SK = "<ENTITY>#<id>".
    A GSI (entity-index: PK=entity, SK=id) is recommended for query_all /
    cross-workspace maintenance jobs; query_all here falls back to a scan
    when that index isn't present, which is fine for low-volume admin use
    but should not sit on any tenant-facing request path.

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
        self.table.put_item(
            Item={"PK": pk, "SK": sk, "entity": entity, "id": item["id"], "data": json.dumps(item)}
        )
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
