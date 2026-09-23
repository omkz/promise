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

The Python side is a single [uv](https://docs.astral.sh/uv/) workspace: the root
`pyproject.toml`/`uv.lock` cover every `packages/*` and `services/*` member in one shared
`.venv`, each installed editable. uv is the only Python dependency/environment manager for
this repo — there is no `requirements.txt` and no bare `pip install`.

```bash
uv sync                              # creates .venv and installs every workspace member, editable

cp services/api/.env.example services/api/.env
cp services/mcp/.env.example services/mcp/.env

uv run python -m promise_app.seed    # seeds the default dev workspace (Andi/Sarah demo data)

(cd services/api && uv run uvicorn promise_api.main:app --reload --port 8000)   # REST
(cd services/mcp && uv run python -m promise_mcp.server)                        # MCP, in another shell
```

Both services default `LOCAL_DATA_DIR` to `../../data` (a shared file at the repo root), so
the REST API, the MCP adapter, and the web app all read/write the same local workspace state
without any extra wiring — a commitment created over MCP shows up over REST and vice versa.

Adding a dependency:

```bash
uv add <package>                 # e.g. uv add pyjwt      — add to a specific member: uv add <package> --package promise-app
uv add --dev <package>           # e.g. uv add --dev mypy — root dev-only tooling (dependency-groups.dev)
```

Both commands update `pyproject.toml` and `uv.lock` together — always commit both.

### Tests

```bash
uv run pytest
```

Lint: `uv run ruff check packages services tests` (config in the root `pyproject.toml`).

Covers: commitment extraction + source provenance, workspace isolation, the full
commitment/action/approval lifecycle, approval enforcement, duplicate-action protection,
swapping the integration provider, the agent-run/step/audit trail, the REST adapter, the MCP
adapter, that REST and MCP observe the same workspace state, and (see below) the MCP Apps UI.

### MCP App: Commitment Card (Alexa+ / MCP Apps UI)

`create_commitment` returns an [MCP Apps](https://github.com/modelcontextprotocol/ext-apps)
(SEP-1865, `io.modelcontextprotocol/ui`) view — a compact Commitment Card an MCP Apps-capable
host (Alexa+, MCP Inspector) can render inline, with a "Handle this" button.

- Tool: `create_commitment` (exposed by both `services/mcp` and, as REST, `POST /api/commitments`)
- UI resource: `ui://promise/commitment-card` (`text/html;profile=mcp-app`), served from the
  built bundle at `services/mcp/ui/commitment-card/dist/index.html`
- MCP endpoint: `http://127.0.0.1:8000/mcp` (Streamable HTTP; see `services/mcp/.env.example` for `HOST`/`PORT`)

The view is a dedicated npm package, `services/mcp/ui/commitment-card`, using the official
[`@modelcontextprotocol/ext-apps`](https://www.npmjs.com/package/@modelcontextprotocol/ext-apps)
client/runtime (`App`) instead of a hand-written postMessage bridge — see `src/main.ts`. Vite
bundles it to a single self-contained `dist/index.html` (no separate JS/CSS assets, since the
view runs in a sandboxed MCP App iframe that shouldn't depend on extra network requests). Its
"Handle this" button calls `app.callServerTool({ name: "handle_commitment", ... })`, the same
tool `services/mcp/promise_mcp/server.py` exposes elsewhere — the view never touches the domain
or database directly. Build it before running the server:

```bash
(cd services/mcp/ui/commitment-card && npm install && npm run build)   # writes dist/index.html
(cd services/mcp && uv run python -m promise_mcp.server)                # serves :8000, MCP endpoint at /mcp
```

Inspect it locally with the [official MCP Inspector](https://github.com/modelcontextprotocol/inspector)
(this is a local dev tool, not a substitute for testing against the real Alexa+ Add-on host):

```bash
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8000/mcp --method tools/list
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8000/mcp --method resources/read --uri "ui://promise/commitment-card"
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8000/mcp --method tools/call \
    --tool-name create_commitment --tool-arg text="I'll send Andi the revised proposal tomorrow morning."
```

Or run `npx @modelcontextprotocol/inspector` (no `--cli`) for the interactive web UI, point it
at the Streamable HTTP endpoint above, and open the Resources tab to preview the card. To point
the official **Alexa+ Add-on Local Inspector** at this server, use the same Streamable HTTP
endpoint: `http://127.0.0.1:8000/mcp` — see Amazon's Alexa+ MCP Add-on documentation for the
inspector's own launch command, which is separate from this repo.

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

## Commitment Detection Engine

`packages/agent/promise_agent/commitment_extraction/` turns free text into a validated
`CommitmentExtraction` — is this a genuine first-person commitment, a suggestion, a
hypothetical, someone else's obligation, a question, quoted speech, or a past action?
(`extractor.py`, `schema.py`). It never touches DynamoDB, FastAPI, or MCP; the only caller
that persists anything is `packages/agent/promise_agent/steps/extraction.py`.

- `provider.py`: `CommitmentExtractionProvider` — `MockCommitmentExtractionProvider`
  (deterministic, rule-based, no AWS credentials required — the default whenever
  `BEDROCK_ENABLED` is false) and `BedrockCommitmentExtractionProvider` (Bedrock Converse
  with tool-use, so the model's output is validated against `RawCommitmentExtraction`
  rather than free-form JSON parsed out of text).
- `temporal.py`: resolves a relative phrase ("tomorrow morning", "next Friday") against an
  explicit reference `datetime` — the model only ever extracts the phrase as written, never
  computes a date itself. An unqualified day defaults to the morning hour
  (`PROMISE_DEFAULT_MORNING_HOUR`, similarly `_AFTERNOON_`/`_EVENING_`/`_NIGHT_`).
- `dedupe.py`: a deterministic (not fuzzy/semantic) duplicate check on normalized
  user + action + contact + due-date window.
- `config.py`: `COMMITMENT_AUTO_CAPTURE_THRESHOLD` (default `0.80`) — below this confidence,
  `promise_app.tools.create_commitment` returns `needs_confirmation=True` and persists
  nothing until re-called with `confirm=True`.

`tools.create_commitment`'s result always distinguishes `detected` (is this a personal
commitment at all) from `persisted` (was a `Commitment` row actually written) — nothing is
ever saved for a suggestion, question, other person's obligation, past action, or a
detection below the confidence threshold.

## Context Retrieval Engine

Given a commitment ("Send the revised proposal to Andi tomorrow morning"), `packages/agent/
promise_agent/context_retrieval/` answers "what documents, messages, and other artifacts are
relevant to completing this?" with ranked, explainable results — independent of FastAPI, MCP,
Alexa+, and DynamoDB, and callable from the agent, REST, and MCP identically.

```
Commitment -> ContextQuery -> ContextRetriever -> search providers (documents, messages, ...) -> dedup -> rank -> ContextItem[]
```

- `schema.py`: `ContextQuery` (workspace/user/commitment/contact/keywords/time window/type
  filter/limit) and `ContextItem` (id, type, title, small `snippet`, `score` in `[0, 1]`,
  `match_reasons`, `source` provenance, `metadata`) — the two ends of the contract.
- `providers.py`: `ContextSearchProvider` — `DocumentSearchProvider`/`MessageSearchProvider`
  adapt the *existing* `IntegrationProvider` abstraction (`packages/integrations`), so a real
  Gmail/Google Drive/Slack/Notion/Microsoft 365 provider needs no new retrieval plumbing —
  only a new `IntegrationProvider` implementation. PostgreSQL full-text and vector/semantic
  search are future `ContextSearchProvider`s; v1 ships none of them, and no vector database or
  embeddings were introduced for this milestone.
- `ranking.py`: `RankingStrategy` — v1's only implementation,
  `DeterministicLexicalRanker`, is pure keyword/metadata matching (contact match, title match,
  phrase/token match, recency; document/message type is a deterministic tie-break, not a
  weighted signal) with fixed weights summing to `1.0`. It never claims semantic relevance —
  every score is traceable to a `match_reasons` string. A semantic/vector ranker later is a
  drop-in replacement behind the same `RankingStrategy` protocol.
- `retriever.py`: `ContextRetriever` fans out to every relevant provider (skipping ones
  excluded by `ContextQuery.types`), drops any candidate outside the query's workspace as
  defense in depth (providers already scope themselves; this never trusts that alone),
  deduplicates an artifact matched by multiple search terms into one item with combined match
  reasons, applies the time-window filter, ranks, and caps at `limit` (default 5 — this never
  dumps the full corpus into agent context). If a provider fails, its error is recorded and
  retrieval continues with what succeeded: `RetrievalOutcome.status` is `partial`, never
  silently reported as `complete`.
- `query_builder.py`: `build_commitment_query` derives keywords from a commitment's
  title/action/description via plain tokenization — no LLM. Deterministic filtering stays
  separate from LLM reasoning (extraction already did any LLM work, upstream of this).

**Application service**: `promise_app.tools.retrieve_commitment_context(workspace_id, user_id,
commitment_id)` loads the commitment (workspace-scoped) and checks `commitment.user_id ==
user_id` before running retrieval, never mutates the commitment, and returns `{"commitment",
"results", "status", "errors", "request_id", "ranking_strategy"}`. REST: `GET
/api/commitments/{id}/context`. MCP: `retrieve_commitment_context` tool. Both are thin
wrappers over the same application service — no retrieval logic in either adapter.

**Agent integration**: the RETRIEVAL step (`steps/retrieval.py`) builds a query from the
commitment and runs `ContextRetriever` fresh each call (so a swapped `IntegrationProvider`,
e.g. in tests, is always the one used); the PLANNING step
(`steps/planning.py::plan_send_revised_document`) now takes a `list[ContextItem]` — never a
raw string — and calls `get_file`/`get_message` for full content only after picking the
top-ranked item, per DOCUMENT CONTENT in the spec (`ContextItem.snippet` is deliberately
small).

**Current limitation**: v1 is deterministic lexical/metadata retrieval (substring and token
overlap over documents/messages already in the local/demo store), not semantic search — it
will miss paraphrases and synonyms a vector search would catch. The `ContextSearchProvider`
and `RankingStrategy` seams exist specifically so semantic/vector retrieval (pgvector,
OpenSearch, embeddings, ...) can be added later as new implementations of those two
interfaces, without `ContextRetriever` or anything above it changing.

**Try it**: `uv run pytest tests/test_context_retrieval.py tests/test_commitment_context_application.py`
covers ranking, filtering, dedup, isolation, and provider-failure behavior in isolation, plus
the commitment -> REST/MCP/agent integration path using the seeded Andi/Sarah demo data.

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
storage layer changes. `packages/agent/promise_agent/llm.py` (document revision) and
`packages/agent/promise_agent/commitment_extraction/provider.py` (commitment detection)
both call Bedrock when `BEDROCK_ENABLED=true` and fall back to a deterministic mock
otherwise — local dev and CI never require live AWS credentials. See `infra/README.md` for
deployment notes.

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
- Context retrieval (`packages/agent/promise_agent/context_retrieval/`) is v1: deterministic
  lexical/keyword + metadata ranking, no semantic/vector search yet. The `ContextSearchProvider`
  and `RankingStrategy` seams are shaped for that to be a later addition without
  `ContextRetriever` or its callers changing — see the Context Retrieval Engine section above.
- DynamoDB adapter is a straightforward single-table CRUD implementation; it has not been
  load-tested and doesn't yet use a GSI for `query_all` (admin/maintenance path only).
- The web UI still has the original single-page demo flow; approvals/activity/connections
  screens described in the target architecture are exposed via REST but not yet all
  represented in dedicated web screens.
