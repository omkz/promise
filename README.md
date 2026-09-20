# PROMISE

PROMISE is an AI follow-through agent. You say a commitment out loud (or type it); PROMISE
detects it, remembers it with provenance, and — when you say "handle it" — retrieves the
relevant context, plans an action, asks for your approval, executes it through an
integration, and records what happened.

Alexa+ is one channel into PROMISE, not the product. The core loop (extraction → memory →
retrieval → planning → approval → execution → completion → audit) lives in a channel-agnostic
application layer that REST, MCP, and any future client all call identically.

## Architecture

```
apps/web            Next.js dashboard (channel: browser)
services/api         FastAPI REST API (channel: web/mobile)
services/mcp          MCP Streamable HTTP adapter (channel: Alexa+ / any MCP client)
packages/domain      Entities + statuses + workspace-scoped repositories
packages/agent        Extraction, retrieval, planning, execution, approval, completion
packages/integrations  IntegrationProvider abstraction + local/demo provider
packages/app           Application services BOTH services/api and services/mcp call
packages/shared        ids, clock, errors, storage adapters (local JSON + DynamoDB)
```

Neither `services/api` nor `services/mcp` contains business logic — both are thin
transport adapters over `packages/app/promise_app/tools.py`, which is the seam the spec
calls the "application layer". Alexa+-specific concerns (voice phrasing, MCP App UI) live
only in `services/mcp`; nothing about Alexa+ leaks into `packages/domain` or `packages/agent`.

Each module carries docstrings explaining the reasoning behind individual design choices
(workspace scoping, idempotency, approval gating, provenance) — start at
`packages/agent/promise_agent/orchestrator.py` for the run loop.

## Local development

Everything is one Python virtualenv with each package/service installed editable, plus the
existing Next.js web app.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/shared -e packages/domain -e packages/integrations \
            -e packages/agent -e packages/app -e services/api -e services/mcp

cp services/api/.env.example services/api/.env
cp services/mcp/.env.example services/mcp/.env

python -m promise_app.seed          # seeds the default dev workspace (Andi/Sarah demo data)

(cd services/api && uvicorn promise_api.main:app --reload --port 8000)   # REST
(cd services/mcp && python server.py)                                    # MCP, in another shell
```

Both services default `LOCAL_DATA_DIR` to `../../data` (a shared file at the repo root), so
the REST API, the MCP adapter, and the web app all read/write the same local workspace state
without any extra wiring — a commitment created over MCP shows up over REST and vice versa.

### Tests

```bash
pip install pytest httpx   # once, alongside the editable installs above
pytest
```

Covers: commitment extraction + source provenance, workspace isolation, the full
commitment/action/approval lifecycle, approval enforcement, duplicate-action protection,
swapping the integration provider, the agent-run/step/audit trail, the REST adapter, the MCP
adapter, and that REST and MCP observe the same workspace state.

### Frontend

```bash
cd web
npm install
cp .env.local.example .env.local
npm run dev
```

Open `http://localhost:3000`.

### Docker

```bash
docker compose up --build
```

Runs the API on `:8000`, the MCP adapter on `:8001`, and the web app on `:3000`, sharing
`./data` as the local store.

## Demo flow

The seeded dev workspace contains the Andi/Sarah scenario (seed data, not architecture — see
`packages/app/promise_app/seed.py`). With the API and web app running:

> I'll send Andi the revised proposal tomorrow morning.

PROMISE detects the commitment and stores it with a source excerpt.

> What did I promise this week?

PROMISE lists it back.

> Handle Andi.

PROMISE runs the agent: it finds the proposal and Sarah's feedback, drafts a revision and an
outbound message, and stops — waiting for your explicit approval — before anything is sent.
Approving executes the send and completes the commitment; the whole run is recorded as an
`AgentRun` with per-step detail and an audit trail.

## AWS mode

```env
STORAGE_BACKEND=dynamodb
AWS_REGION=us-east-1
DYNAMODB_TABLE=PROMISE
S3_BUCKET=promise-documents
BEDROCK_ENABLED=true
```

`packages/shared/promise_shared/store/dynamodb.py` implements a single-table adapter behind
the same `EntityStore` interface the local JSON store implements, so nothing above the
storage layer changes. `packages/agent/promise_agent/llm.py` calls Bedrock when
`BEDROCK_ENABLED=true` and falls back to a deterministic mock otherwise — local dev and CI
never require live AWS credentials. See `infra/README.md` for deployment notes.

## Known limitations / next steps

- Auth is not implemented: workspace/user resolution is header-based
  (`X-Workspace-Id`/`X-User-Id`) with a seeded default workspace as fallback. A real deployment
  needs Cognito/JWT-based auth wired into `services/api/promise_api/deps.py` and
  `services/mcp` before handling real user data.
- Only one integration provider exists (`LocalIntegrationProvider`, demo/local data). Gmail,
  Drive, Slack, etc. are additive: implement `IntegrationProvider` and register it — no
  changes needed in `packages/agent` or `packages/app`.
- `IntegrationAccount.secret_ref` is a placeholder pointer; there is no real Secrets Manager
  integration yet, and no OAuth flow.
- Search is exact-substring only; the interface (`IntegrationProvider.search_files` /
  `search_messages`) is shaped to support semantic/vector search later without callers
  changing.
- DynamoDB adapter is a straightforward single-table CRUD implementation; it has not been
  load-tested and doesn't yet use a GSI for `query_all` (admin/maintenance path only).
- The web UI still has the original single-page demo flow; approvals/activity/connections
  screens described in the target architecture are exposed via REST but not yet all
  represented in dedicated web screens.
