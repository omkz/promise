"""Deploy the `UserOwnedIndex` GSI (see `promise_shared.store.dynamodb_schema`)
and backfill it onto rows written before the index existed.

Safe for BOTH a brand-new table and an existing one already carrying data --
it never deletes or recreates the table.

1. Index creation -- checks the table's current GSIs first:
   - Already present: no-op, moves straight to backfill.
   - Missing: `UpdateTable` with `GlobalSecondaryIndexUpdates: [{"Create": ...}]`,
     DynamoDB's supported online path for adding a GSI to an existing table
     (the table stays available for reads/writes throughout; DynamoDB
     backfills the *index* itself from existing items with the right key
     attributes, in the background). Then polls `describe_table` until the
     new index's own `IndexStatus` is `ACTIVE`.

2. Attribute backfill (`--skip-backfill` to opt out) -- DynamoDB's own online
   GSI backfill above only indexes items that already HAVE `GSI1PK`/`GSI1SK`.
   Rows written by an older deploy never got those attributes at all, so
   they're silently absent from `query_index` results no matter how long you
   wait. `DynamoEntityStore.backfill_user_owned_index` (see that module) closes
   that gap: scans each user-owned entity once, derives the missing keys, and
   writes only those two attributes via a targeted `UpdateExpression` -- never
   touches anything else on the item. Idempotent: safe to rerun (e.g. after a
   partial failure, or on a schedule) since already-correct rows are left
   untouched.

Usage:
    uv run python infra/deploy_gsi.py --table PROMISE --region us-east-1
    uv run python infra/deploy_gsi.py --table PROMISE --region us-east-1 --skip-backfill
"""

from __future__ import annotations

import argparse
import time

from promise_shared.store.dynamodb_schema import GSI_NAME, add_gsi_update
from promise_shared.store.index_keys import USER_OWNED_ENTITIES


def _index_status(client, table_name: str) -> str | None:
    table = client.describe_table(TableName=table_name)["Table"]
    for gsi in table.get("GlobalSecondaryIndexes", []):
        if gsi["IndexName"] == GSI_NAME:
            return gsi["IndexStatus"]
    return None


def _wait_for_index_active(client, table_name: str, *, poll_seconds: float = 5, max_attempts: int = 60) -> None:
    for _ in range(max_attempts):
        status = _index_status(client, table_name)
        if status == "ACTIVE":
            return
        time.sleep(poll_seconds)
    raise TimeoutError(f"{GSI_NAME} on {table_name!r} did not become ACTIVE in time")


def deploy(table_name: str, region: str, *, backfill: bool = True) -> None:
    import boto3
    from promise_shared.aws_config import boto_config

    client = boto3.client("dynamodb", region_name=region, config=boto_config())

    if _index_status(client, table_name) is None:
        client.update_table(**add_gsi_update(table_name))
        print(f"Requested creation of {GSI_NAME} on {table_name!r}; waiting for it to become ACTIVE...")
        _wait_for_index_active(client, table_name)
        print(f"{GSI_NAME} is ACTIVE.")
    else:
        print(f"{GSI_NAME} already exists on {table_name!r}.")

    if not backfill:
        return

    from promise_shared.store.dynamodb import DynamoEntityStore

    store = DynamoEntityStore(table_name=table_name, region=region)
    for entity in sorted(USER_OWNED_ENTITIES):
        counts = store.backfill_user_owned_index(entity)
        print(f"backfill {entity}: {counts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", default="PROMISE")
    parser.add_argument("--region", required=True)
    parser.add_argument("--skip-backfill", action="store_true", help="only create the GSI, don't backfill existing rows")
    args = parser.parse_args()
    deploy(args.table, args.region, backfill=not args.skip_backfill)
