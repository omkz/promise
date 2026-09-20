# AWS deployment notes

1. Create a DynamoDB table named `PROMISE` with partition key `PK` (string) and sort key `SK`
   (string). See `packages/shared/promise_shared/store/dynamodb.py` for the key scheme
   (`WORKSPACE#<workspace_id>` / `<ENTITY>#<id>`).
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
