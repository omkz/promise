# services/mcp on Amazon Bedrock AgentCore Runtime

Preparation only -- this documents how to deploy `services/mcp` to AgentCore Runtime; no
deploy has been run from this repository. Reuses the existing Dockerfile/entrypoint as-is
(`promise_mcp.server`); nothing here changes application code or business logic.

## AgentCore's MCP container contract

AgentCore Runtime, configured for the MCP protocol, requires (see AWS's
[MCP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp-protocol-contract.html)):

| Requirement | This repo |
| --- | --- |
| Listen on `0.0.0.0:8000` | `services/mcp/Dockerfile` now sets `ENV HOST=0.0.0.0` / `ENV PORT=8000` -- `promise_mcp/server.py`'s `uvicorn.run(host=os.getenv("HOST", ...), port=int(os.getenv("PORT", ...)))` picks these up unconditionally, no orchestrator-supplied override needed. |
| Streamable-HTTP, stateless (`stateless_http=True`) | Already set: `app = mcp.streamable_http_app(stateless_http=True, ...)`. Unchanged. |
| `/mcp` as the POST RPC path | The `mcp` SDK's own default (`streamable_http_path="/mcp"`), never overridden here. |
| ARM64 container | No amd64-specific step in the Dockerfile -- the `python:3.12-slim`/`node:22-slim` bases and the pinned `uv` image (`ghcr.io/astral-sh/uv:0.12.18@sha256:...`) are all multi-arch; building with `--platform linux/arm64` resolves the arm64 manifest for each (verified with `docker buildx imagetools inspect` against the pinned uv digest). |

Why `HOST=0.0.0.0` also matters beyond the socket bind: `mcp.server.mcpserver`'s
`streamable_http_app(host=...)` auto-enables DNS-rebinding protection (a strict
`allowed_hosts`/`allowed_origins` allowlist) whenever `host` is itself a loopback address.
AgentCore's `InvokeAgentRuntime` proxy will never send a `Host` header of `127.0.0.1` --
with the old default, a deployed container would have silently rejected every real request
even if something else had fixed the socket bind. `services/mcp/promise_mcp/server.py`
reads the same `HOST` env var for both, so setting it once fixes both.

## Local development flow (unchanged)

- `uv run python -m promise_mcp.server` -- reads `services/mcp/.env` (`HOST=127.0.0.1`,
  `PORT=8001`), exactly as before.
- `docker compose up promise-mcp` -- `docker-compose.yml` already overrides
  `HOST=0.0.0.0`/`PORT=8000` as container environment, so behavior there hasn't changed;
  it's now also consistent with the Dockerfile's own baked-in defaults.

## Build the ARM64 image

Build context is the repo root (the Dockerfile's `COPY` paths are root-relative -- same
context `docker-compose.yml` already uses):

```
aws ecr create-repository --repository-name promise-mcp --region <REGION>   # one-time
aws ecr get-login-password --region <REGION> \
  | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com

docker buildx build --platform linux/arm64 \
  -f services/mcp/Dockerfile \
  -t <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/promise-mcp:latest \
  --push .
```

## IAM

Two roles' worth of permissions land on the *same* execution role, since the container's
own code (DynamoDB/S3/Secrets Manager/Bedrock calls) runs under whatever role AgentCore
Runtime assumes:

1. `infra/iam-agentcore-trust-policy.json` -- trust relationship letting
   `bedrock-agentcore.amazonaws.com` assume the role.
2. `infra/iam-agentcore-execution-role-policy.json` -- AWS's own documented baseline
   (ECR pull, its CloudWatch Logs group, X-Ray, the `bedrock-agentcore` metrics namespace,
   workload access tokens, Bedrock model invocation for AgentCore's own use).
3. `infra/iam-app-runtime-policy.json` -- this app's own AWS access (DynamoDB table+GSI,
   S3 documents prefix, Secrets Manager prefix, Bedrock for document revision/commitment
   extraction). See `infra/README.md` point 8.

Fill in `<REGION>`/`<ACCOUNT_ID>`/`<AGENT_NAME>`/`<TABLE_NAME>`/`<BUCKET>` in all three,
create the role with (1) as its trust policy, and attach (2) and (3) to it.

## Required environment variables

Same variables `services/mcp/.env.example` documents for a real deployment (not the local
dev defaults) -- set these via `--environment-variables` on `create-agent-runtime` (or
`update-agent-runtime` later), since there is no docker-compose here to supply them:

```
STORAGE_BACKEND=dynamodb
DYNAMODB_TABLE=<TABLE_NAME>
AWS_REGION=<REGION>
BLOB_STORE_BACKEND=s3
S3_BUCKET=<BUCKET>
SECRET_STORE_BACKEND=aws
SECRETS_MANAGER_PREFIX=promise/
BEDROCK_ENABLED=true            # optional -- false keeps the deterministic mock revision
BEDROCK_MODEL_ID=amazon.nova-lite-v1:0
AUTH_MODE=oidc                  # local dev identity has no place in a real deployment
COGNITO_ISSUER=...
COGNITO_CLIENT_ID=...
CORS_ORIGINS=<the web app's real origin>
```

`HOST`/`PORT` are **not** listed -- the Dockerfile now bakes in the values AgentCore
requires, so there's nothing to override.

## Exact deploy command

Not run from this repository (preparation only). Once the image above is pushed and both
IAM roles exist:

```
aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name promise_mcp \
  --agent-runtime-artifact '{"containerConfiguration": {"containerUri": "<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/promise-mcp:latest"}}' \
  --role-arn arn:aws:iam::<ACCOUNT_ID>:role/<AgentCoreExecutionRoleName> \
  --network-configuration networkMode=PUBLIC \
  --protocol-configuration serverProtocol=MCP \
  --environment-variables '{"STORAGE_BACKEND":"dynamodb","DYNAMODB_TABLE":"<TABLE_NAME>","AWS_REGION":"<REGION>","BLOB_STORE_BACKEND":"s3","S3_BUCKET":"<BUCKET>","SECRET_STORE_BACKEND":"aws","AUTH_MODE":"oidc"}' \
  --region <REGION>
```

This returns an agent runtime ARN (`arn:aws:bedrock-agentcore:<REGION>:<ACCOUNT_ID>:runtime/promise_mcp-...`).
Clients then call it through the `InvokeAgentRuntime` API / the
`https://bedrock-agentcore.<REGION>.amazonaws.com/runtimes/<encoded-arn>/invocations` endpoint,
never the container directly.

## AWS prerequisites still required (not done here)

- An ECR repository (`aws ecr create-repository`) and a pushed ARM64 image.
- The DynamoDB table + GSI, S3 bucket, and Secrets Manager prefix from `infra/README.md`
  points 1-1a/2/7.
- The IAM role described above (trust policy + both permission policies attached).
- `AUTH_MODE=oidc` + a real Cognito (or other OIDC) issuer -- `infra/README.md` point 6
  already flags this as the top pre-production blocker; it's not specific to AgentCore.
- Whatever invokes this runtime (Alexa+ account-linking, another MCP client) needs its own
  OAuth/SigV4 setup against the runtime ARN -- out of scope for this infrastructure task.

## Validated locally

- `docker buildx imagetools inspect` on the pinned `uv` base image confirms it's a
  multi-arch index with a `linux/arm64` manifest (not an amd64-only digest that would
  silently break a `--platform linux/arm64` build).
- `docker buildx build --platform linux/arm64 -f services/mcp/Dockerfile .` (repo root
  context) builds successfully end to end (this surfaced and led to fixing a real,
  pre-existing bug: `packages/auth` was never `COPY`'d into either Dockerfile even though
  `promise_app` depends on it -- `uv sync` failed on both api and mcp images before that fix,
  regardless of platform).
- Ran the built arm64 image under QEMU emulation with **no environment overrides at all**
  (`docker run -p 18000:8000 <image>`) and confirmed from the host:
  - `GET /health` -> `{"status":"ok","service":"promise-mcp","mcp":"/mcp"}`
  - `POST /mcp` (`tools/list`) -> a valid JSON-RPC response listing all 15 tools
  - `POST /mcp` with a caller-supplied `Mcp-Session-Id` header -> accepted (200), not
    rejected -- the stateless-mode requirement AgentCore's session-isolation proxy depends on
  - `docker inspect` confirms the image's own baked-in `HOST=0.0.0.0`/`PORT=8000`, matching
    what actually got bound (`Uvicorn running on http://0.0.0.0:8000` in the container logs)
- Full backend test suite (`uv run pytest`, 542 tests) and `uv run ruff check` re-run
  unchanged and green -- no application code moved or edited, only the two Dockerfiles and
  this documentation.
