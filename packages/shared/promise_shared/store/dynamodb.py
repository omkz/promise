from __future__ import annotations

import json
from typing import Any

from promise_shared.errors import ConflictError

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

        from ..aws_config import boto_config

        self._boto3 = boto3
        self.table = boto3.resource("dynamodb", region_name=region, config=boto_config()).Table(table_name)

    @staticmethod
    def _keys(entity: str, workspace_id: str, item_id: str) -> tuple[str, str]:
        return f"WORKSPACE#{workspace_id}", f"{entity.upper()}#{item_id}"

    def put(self, entity: str, item: dict, *, expected_status: str | None = None) -> dict:
        if "id" not in item or "workspace_id" not in item:
            raise ValueError("entity rows must include 'id' and 'workspace_id'")
        pk, sk = self._keys(entity, item["workspace_id"], item["id"])
        row: dict[str, Any] = {"PK": pk, "SK": sk, "entity": entity, "id": item["id"], "data": json.dumps(item)}
        if entity in USER_OWNED_ENTITIES and item.get("user_id"):
            gsi_pk, gsi_sk = user_owned_index_keys(entity, item["workspace_id"], item["user_id"], item.get("created_at", ""), item["id"])
            row["GSI1PK"], row["GSI1SK"] = gsi_pk, gsi_sk
        if "status" in item:
            # Promoted to a top-level attribute (in addition to living inside the
            # opaque `data` blob, unchanged) purely so `expected_status` below has
            # something DynamoDB can evaluate a ConditionExpression against --
            # `data` itself is a JSON string DynamoDB can't look inside. Written
            # unconditionally (whether or not this particular call passes
            # `expected_status`) so it's always up to date for the *next* call
            # that does.
            row["status"] = item["status"]
        put_kwargs: dict[str, Any] = {"Item": row}
        if expected_status is not None:
            from boto3.dynamodb.conditions import Attr

            put_kwargs["ConditionExpression"] = Attr("status").eq(expected_status)
        try:
            self.table.put_item(**put_kwargs)
        except Exception as exc:
            from botocore.exceptions import ClientError

            if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ConflictError(
                    f"{entity} '{item['id']}' is not in the expected state (expected status {expected_status!r})"
                ) from exc
            raise
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

    def query_index(
        self, index_name: str, partition_key: str, *, sort_key_prefix: str | None = None, limit: int | None = None
    ) -> list[dict]:
        """Query a named GSI by partition key, optionally narrowed by a sort-key
        `begins_with` prefix and/or capped with `Limit`. Always a DynamoDB Query
        against `IndexName`, never a Scan -- this is the method the
        ownership-filtered list access patterns (`Repository.list_user_owned`)
        go through instead of `query` + Python filtering.

        No generic `FilterExpression` passthrough: every current caller's extra
        narrowing (e.g. `status=`) runs in Python in `Repository.list_user_owned`
        over the already-tiny, already-owned rows this returns -- simpler than
        wiring an arbitrary expression builder for filters that never touch more
        than a handful of rows per user. `sort_key_prefix`/`limit` cover the
        access patterns that actually exist today; nothing stops a future
        `FilterExpression` kwarg being added here if that changes, as long as it
        stays additive *after* this key condition, never a replacement for it.
        """
        from boto3.dynamodb.conditions import Key

        condition = Key("GSI1PK").eq(partition_key)
        if sort_key_prefix:
            condition = condition & Key("GSI1SK").begins_with(sort_key_prefix)
        kwargs: dict[str, Any] = {"IndexName": index_name, "KeyConditionExpression": condition}
        if limit is not None:
            kwargs["Limit"] = limit
        resp = self.table.query(**kwargs)
        return [self._decode(x) for x in resp.get("Items", [])]

    def query_all(self, entity: str) -> list[dict]:
        scan = self.table.scan(FilterExpression="entity = :e", ExpressionAttributeValues={":e": entity})
        return [self._decode(x) for x in scan.get("Items", [])]

    def backfill_user_owned_index(self, entity: str) -> dict[str, int]:
        """One-time/rerunnable maintenance job: populate `GSI1PK`/`GSI1SK` on
        existing `entity` rows written before the `UserOwnedIndex` GSI existed.
        DynamoDB's online GSI backfill only indexes items that already HAVE the
        index's key attributes at creation time -- rows written by an older
        deploy never got them, so they're silently absent from `query_index`
        results until they're rewritten some other way. This closes that gap
        directly. See infra/README.md's migration note and `infra/deploy_gsi.py`
        (extend that script rather than adding a second entry point).

        Admin/maintenance only, like `query_all` -- built on the same Scan,
        never call this on a tenant-facing request path.

        Idempotent and safe to retry: scans once (paging through the whole
        entity via `ExclusiveStartKey`), and for each row compares its current
        `GSI1PK`/`GSI1SK` against what `user_owned_index_keys` would derive for
        it right now. Already-correct rows are left untouched -- a rerun after
        a partial failure only re-writes the rows still missing/wrong. Each
        write is a targeted `UpdateExpression` setting only those two
        attributes (never `put_item`), so it can never clobber any other
        attribute on the item.
        """
        if entity not in USER_OWNED_ENTITIES:
            raise ValueError(f"'{entity}' is not a user-owned entity (see index_keys.USER_OWNED_ENTITIES)")

        counts = {"scanned": 0, "updated": 0, "already_correct": 0, "skipped_no_user_id": 0}
        scan_kwargs: dict[str, Any] = {"FilterExpression": "entity = :e", "ExpressionAttributeValues": {":e": entity}}
        while True:
            resp = self.table.scan(**scan_kwargs)
            for raw in resp.get("Items", []):
                counts["scanned"] += 1
                item = self._decode(raw)
                user_id = item.get("user_id")
                if not user_id:
                    counts["skipped_no_user_id"] += 1
                    continue
                expected_pk, expected_sk = user_owned_index_keys(
                    entity, item["workspace_id"], user_id, item.get("created_at", ""), item["id"]
                )
                if raw.get("GSI1PK") == expected_pk and raw.get("GSI1SK") == expected_sk:
                    counts["already_correct"] += 1
                    continue
                self.table.update_item(
                    Key={"PK": raw["PK"], "SK": raw["SK"]},
                    UpdateExpression="SET GSI1PK = :pk, GSI1SK = :sk",
                    ExpressionAttributeValues={":pk": expected_pk, ":sk": expected_sk},
                )
                counts["updated"] += 1
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        return counts

    def delete(self, entity: str, workspace_id: str, item_id: str) -> None:
        pk, sk = self._keys(entity, workspace_id, item_id)
        self.table.delete_item(Key={"PK": pk, "SK": sk})

    @staticmethod
    def _decode(item: dict[str, Any]) -> dict[str, Any]:
        if "data" in item:
            return json.loads(item["data"])
        return item
