"""Deploy the `UserOwnedIndex` GSI (see `promise_shared.store.dynamodb_schema`).

Safe for BOTH a brand-new table and an existing one already carrying data --
it never deletes or recreates the table. It checks the table's current GSIs
first:

- Index already present: no-op.
- Index missing: `UpdateTable` with `GlobalSecondaryIndexUpdates: [{"Create": ...}]`,
  DynamoDB's supported online path for adding a GSI to an existing table (the
  table stays available for reads/writes throughout; DynamoDB backfills the
  index from existing items in the background). This does NOT retroactively
  write `GSI1PK`/`GSI1SK` onto rows already in the table from before this
  index existed for entities in `USER_OWNED_ENTITIES` -- an item only gets
  those attributes on its next `put_item` (`DynamoEntityStore.put`). Existing
  rows will simply not appear in `query_index` results until they're next
  written (e.g. any `update()`/`save()` call). See infra/README.md.

Usage:
    uv run python infra/deploy_gsi.py --table PROMISE --region us-east-1
"""

from __future__ import annotations

import argparse

from promise_shared.store.dynamodb_schema import GSI_NAME, add_gsi_update


def deploy(table_name: str, region: str) -> None:
    import boto3

    client = boto3.client("dynamodb", region_name=region)
    table = client.describe_table(TableName=table_name)["Table"]
    existing = {gsi["IndexName"] for gsi in table.get("GlobalSecondaryIndexes", [])}

    if GSI_NAME in existing:
        print(f"{GSI_NAME} already exists on {table_name!r} -- nothing to do.")
        return

    client.update_table(**add_gsi_update(table_name))
    print(f"Requested creation of {GSI_NAME} on {table_name!r}. DynamoDB backfills it online; "
          f"poll `describe_table` until its IndexStatus is ACTIVE before relying on it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default="PROMISE")
    parser.add_argument("--region", required=True)
    args = parser.parse_args()
    deploy(args.table, args.region)
