# AWS deployment notes

1. Create a DynamoDB table named `PROMISE` with partition key `PK` (string) and sort key `SK`
   (string), billing mode `PAY_PER_REQUEST`. See `packages/shared/promise_shared/store/dynamodb.py`
   for the key scheme (`WORKSPACE#<workspace_id>` / `<ENTITY>#<id>`), and step 1a below for the
   `UserOwnedIndex` GSI every new table should be created with.

1a. **`UserOwnedIndex` GSI** -- `promise_shared.store.dynamodb_schema.gsi_definition()` is the
   source of truth for its shape; `promise_shared.store.index_keys` documents why it exists.

   ```
   GSI name:        UserOwnedIndex
   Partition key:   GSI1PK  ("WORKSPACE#<workspace_id>#USER#<user_id>")
   Sort key:        GSI1SK  ("<ENTITY>#<created_at>#<id>", e.g. "COMMITMENT#2026-.../com_1")
   Projection:      INCLUDE, NonKeyAttributes=["data"]  (PK/SK are always projected for free)
   ```

   **Access patterns it serves** -- both currently workspace-scan-then-Python-filter without it:
   - **Commitments**: `search_commitments` (workspace + user, then in-memory status/text
     filter over the caller's own rows only -- never the whole workspace's commitments).
   - **Integration accounts**: `list_integration_accounts` (workspace + user).

   Only `commitment` and `integration_account` rows get `GSI1PK`/`GSI1SK` written
   (`index_keys.USER_OWNED_ENTITIES`) -- `Action`/`Approval` have no direct owner field
   (ownership is transitive through `Action.commitment_id`, see the README's "Resource
   ownership" section) and `AgentRun`'s workspace-scan-then-filter list stays as is; none of
   these get a GSI here, deliberately -- see the root README's "DynamoDB access patterns"
   section for the reasoning.

   **New table**: create it with the GSI already in `GlobalSecondaryIndexes` --
   `promise_shared.store.dynamodb_schema.table_definition()` returns the full `CreateTable`
   payload (table + index together).

   **Existing table** (this application is not assumed to be greenfield): do **not** delete
   or recreate the table. Run:

   ```
   uv run python infra/deploy_gsi.py --table PROMISE --region <region>
   ```

   This does two things, in order:
   1. **Create the index if missing.** Checks whether `UserOwnedIndex` already exists and, if
      not, issues an `UpdateTable` with `GlobalSecondaryIndexUpdates: [{"Create": ...}]` --
      DynamoDB's supported way to add a GSI to a live table online (table stays available
      throughout). It then polls `describe_table` until the new index's own `IndexStatus` is
      `ACTIVE` before moving on. DynamoDB's own online backfill here only indexes items that
      *already have* `GSI1PK`/`GSI1SK` -- which rows written before this code shipped don't.
   2. **Backfill attribute values onto existing rows** (skip with `--skip-backfill`). Calls
      `DynamoEntityStore.backfill_user_owned_index` for each entity in `USER_OWNED_ENTITIES`
      (`commitment`, `integration_account`): scans that entity once (paging through the whole
      table), and for each row whose `GSI1PK`/`GSI1SK` are missing or don't match what
      `index_keys.user_owned_index_keys` would derive for it now, issues a targeted
      `UpdateExpression` that sets only those two attributes -- never a `put_item`, so it can
      never touch anything else on the row. Idempotent and safe to rerun: a row whose keys are
      already correct is left alone, so re-running after a partial failure (or just as a
      periodic safety net) only touches what's still wrong.

2. Create an S3 bucket for document binary artifacts and set `S3_BUCKET` + `BLOB_STORE_BACKEND=s3`
   (`S3_DOCUMENTS_PREFIX`, default `documents/`, is optional). Keep it private -- `S3BlobStore`
   (`packages/shared/promise_shared/blobs/s3_blob_store.py`) never sets a public-read ACL, and
   nothing else in this repository needs to be able to reach it directly. See the root README's
   "Document storage" section (under "Gmail Integration") for the full design.
3. Use Amazon Bedrock for model-assisted revision. Keep `BEDROCK_ENABLED=false` for
   deterministic local/dev/CI runs; set it to `true` plus `BEDROCK_MODEL_ID`/`AWS_REGION` in
   deployed environments.
4. Deploy `services/api` (FastAPI, REST) and `services/mcp` (MCP Streamable HTTP) as separate
   containers — see `services/api/Dockerfile` and `services/mcp/Dockerfile`. The MCP endpoint
   is `/mcp`; Streamable HTTP is required by AgentCore-style MCP runtimes. Both services can
   point at the same DynamoDB table so REST and MCP observe the same workspace state.
5. Put the web UI (`web/`) on Amplify, ECS, or another HTTPS host, pointed at the REST API's
   public URL via `NEXT_PUBLIC_API_URL`.
6. Add Cognito/account-linking before connecting real user data — today, workspace/user
   resolution is header-based with a seeded dev workspace fallback (see
   `services/api/promise_api/deps.py`). This is the top priority before any multi-tenant
   production use.
7. `IntegrationAccount` secrets (Gmail OAuth tokens today) are wired up: set
   `SECRET_STORE_BACKEND=aws` (region from `AWS_REGION`, secret name prefix from
   `SECRETS_MANAGER_PREFIX`, default `promise/`) so `AwsSecretsManagerStore`
   (`packages/shared/promise_shared/secrets/aws_secrets_manager.py`) is used instead of the
   dev-only local file store — `IntegrationAccount.secret_ref` only ever holds a pointer into
   it, never a raw token. See the root README's "Gmail Integration" section (points 3-6) for
   OAuth credential setup, required scopes, and the full connect/callback flow.

See the official AWS AgentCore MCP runtime documentation for the current deployment contract.
