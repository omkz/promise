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
   or recreate the table. Run `uv run python infra/deploy_gsi.py --table PROMISE --region
   <region>` -- it checks whether the index already exists and, if not, issues an `UpdateTable`
   with `GlobalSecondaryIndexUpdates: [{"Create": ...}]`, DynamoDB's supported way to add a GSI
   to a live table online (table stays available throughout; DynamoDB backfills the index from
   existing items in the background -- poll `describe_table`'s `IndexStatus` until `ACTIVE`).
   One caveat: that backfill does not retroactively add `GSI1PK`/`GSI1SK` to rows written
   *before* the index existed -- see `infra/deploy_gsi.py`'s docstring for why, and re-save
   (any `update()`) old commitment/integration-account rows if they need to show up in the
   indexed list before their next natural write.

2. Create an S3 bucket for document objects and set `S3_BUCKET`. (Document storage-key wiring
   to S3 is not implemented yet — see README "Known limitations".)
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
7. Move `IntegrationAccount` secrets (OAuth tokens) into AWS Secrets Manager and store only
   the secret's reference (`secret_ref`) in DynamoDB — the domain model already carries this
   field but nothing wires it up yet.

See the official AWS AgentCore MCP runtime documentation for the current deployment contract.
