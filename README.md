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
packages/auth           AuthProvider abstraction, JWT/JWKS validation, principal resolution
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
e.g. in tests, is always the one used) and hands its ranked `list[ContextItem]` — never a raw
string — straight to the PLANNING step. See the next section for what PLANNING does with it.

**Current limitation**: v1 is deterministic lexical/metadata retrieval (substring and token
overlap over documents/messages already in the local/demo store), not semantic search — it
will miss paraphrases and synonyms a vector search would catch. The `ContextSearchProvider`
and `RankingStrategy` seams exist specifically so semantic/vector retrieval (pgvector,
OpenSearch, embeddings, ...) can be added later as new implementations of those two
interfaces, without `ContextRetriever` or anything above it changing.

**Try it**: `uv run pytest tests/test_context_retrieval.py tests/test_commitment_context_application.py`
covers ranking, filtering, dedup, isolation, and provider-failure behavior in isolation, plus
the commitment -> REST/MCP/agent integration path using the seeded Andi/Sarah demo data.

## Action Planning Engine

```
Commitment -> Context Retrieval -> ActionPlanner -> ActionPlan -> Action (proposed) -> Approval -> Execution -> Completion
```

`packages/agent/promise_agent/action_planning/` separates *generic* action planning from the
specific workflows v1 actually supports — the same separation `commitment_extraction` and
`context_retrieval` already establish elsewhere in `packages/agent`.

- `planner.py`: `ActionPlanner` — receives a `Commitment`, its `Contact`, and the ranked
  `list[ContextItem]` from retrieval; returns a proposed `Action`. An `ActionPlanner` never
  executes a side effect, never requests approval, never marks a commitment complete, and
  never touches FastAPI/MCP — those stay in `steps/approval.py`, `steps/execution.py`,
  `steps/completion.py`, called only by `AgentOrchestrator`.
- `schema.py`: `ActionPlan` — a small, explainable, pre-persistence structure
  (`action_type`, `summary`, `rationale`, `approval_required`, `target_contact_id`,
  `supporting_context_ids` — which `ContextItem`s justified the plan —, `payload`). It never
  duplicates a field `Action` already owns; `payload`/`action_type` map straight onto the
  `Action` a planner persists. `approval_required` is informational only (derived from
  `promise_domain.enums.ACTION_TYPES_REQUIRING_APPROVAL`) — it never gates anything; approval
  stays unconditional, exactly as before (see SECURITY below).
- **Two concrete planners, mutually exclusive by construction** — a plain
  "send/email/share/deliver/submit/forward" verb never, by itself, implies a revision:
  - `send_message_planner.py`: `SendRevisedDocumentPlanner` (alias: `SendMessagePlanner`, for
    backward compatibility) — "send the *revised/updated* X". The only planner that ever calls
    `llm.revise_document`; `supports()` requires both a send-type verb *and* a revision signal
    (revise/revised/revision/update/updated/incorporate feedback/...; see
    `action_planning/_signals.py`).
  - `send_existing_document_planner.py`: `SendExistingDocumentPlanner` — "send X" with no
    revision language. Attaches the document exactly as retrieval found it — no
    `llm.revise_document` call, no new `Document` row. `supports()` requires a send-type verb
    *and the absence* of a revision signal.

  Nothing Andi/Sarah/filename-specific is hard-coded into either: both recognize the *general
  shape* of a commitment via `action_planning/_signals.py`, and `plan()` works off whichever
  `ContextItem`s retrieval ranked highest for *this* commitment. See
  `tests/test_action_planning.py::
  test_revision_planner_selection_is_not_hard_coded_to_any_specific_name_or_file` and
  `test_existing_document_planner_selection_is_not_hard_coded_to_any_specific_name_or_file`
  for the regression proof (a different contact, different document, different workspace —
  same behavior).
- `message_composer.py`: `MessageComposer` — v1 ships `DeterministicMessageComposer`, with two
  methods: `compose` (for a revision — derives the subject from the commitment's own title and
  the body from the real `changes` list `llm.revise_document` already computed, never
  inventing a factual claim about what changed) and `compose_existing_document` (for a plain
  send — no "summary of changes" section at all, since nothing was revised). Both replace what
  used to be a permanently hard-coded `subject="Revised proposal"` / templated body. A future
  context-aware or Bedrock-assisted composer is a new `MessageComposer` implementation, not a
  change to either planner.
- `selection.py`: `select_planner(commitment)` — deterministic, no LLM (an LLM isn't needed to
  choose between two supported planners and "unsupported"). Raises `PlanningError` ("PROMISE
  does not yet know how to execute this commitment.") for any commitment type no registered
  planner recognizes — never fabricates an action for it.

**Orchestrator**: `AgentOrchestrator.run_handle_commitment`'s PLANNING step now calls
`select_planner(commitment)` then `planner.plan(...)` directly — no planner-specific
branching lives in the orchestrator itself, which stays focused on run/step lifecycle,
audit, and the approval/execution/completion sequence (all unchanged).
`packages/agent/promise_agent/steps/planning.py::plan_send_revised_document` remains as a
thin, `SendRevisedDocumentPlanner`-specific compatibility entry point.

**Current limitation**: v1 supports exactly two action types, both `SEND_MESSAGE` — "send the
revised document" and "send the existing document" — to a verified contact. Any other
commitment (e.g. "call Sam") fails planning with a structured `PlanningError` rather than a
fabricated action; adding a third planner is registering a new `ActionPlanner` in
`selection.py`'s list, no orchestrator changes needed. The orchestrator's result dict
(`document`/`changes`/`draft`/`action` keys) is still shaped around what these two planners
both happen to return — a planner producing a genuinely different shape would need that
generalized too.

**LLM reliability**: `packages/agent/promise_agent/llm.py::revise_document` follows the same
rule as the commitment-extraction Bedrock provider: `BEDROCK_ENABLED=false` uses the
deterministic `mock_revision` directly (not as a failure fallback), and
`BEDROCK_ENABLED=true` + a real Bedrock failure raises `promise_shared.errors.
LLMProviderError` (with a classified `retryable` flag) instead of silently substituting the
mock — the agent run fails clearly, surfaced over REST as `503` with `{"provider",
"retryable"}`. Local tests stay fully deterministic (`BEDROCK_ENABLED` unset/false).

## Identity & Authorization

```
HTTP/MCP request -> Authentication (AuthProvider) -> AuthenticatedPrincipal -> Authorization (require) -> Application Service -> Domain/Repository
```

The application layer never trusts a `user_id` from a query parameter, JSON body, MCP tool
argument, or `X-User-Id`-style header — identity always comes from
`packages/app/promise_app/identity.py::authenticate`, called once per request by both
`services/api/promise_api/deps.py::get_principal` and `services/mcp/promise_mcp/server.py`'s
`_authenticate`. Neither adapter re-implements it; both call the exact same function.

- **`packages/auth/` (`promise_auth`)**: the `AuthProvider` abstraction. `AuthRequest` (raw,
  transport-agnostic input: an `Authorization` header, an optional workspace hint, and a
  local-only dev-user override) -> `TokenClaims` (verified subject + scopes — no workspace
  yet). Two implementations:
  - `LocalAuthProvider` (`AUTH_MODE=local`, the default): deterministic dev identity from
    `DEV_USER_ID`/`DEV_WORKSPACE_ID`. Not production security — isolated behind the same
    interface `OIDCAuthProvider` implements, so nothing above this layer needs to know which
    is active.
  - `OIDCAuthProvider` (`AUTH_MODE=oidc`): validates a `Bearer` access token against JWKS —
    signature, issuer, expiration, audience (when configured), `token_use`, and required
    scopes. Compatible with Amazon Cognito User Pools and any standard OIDC provider. Never
    decodes a JWT without verifying its signature first, and never falls back to local
    identity on any failure — every failure raises a specific, classified error (see below).
- **Principal resolution** (`packages/app/promise_app/identity.py`): `TokenClaims` alone
  aren't enough to act — `AuthenticatedPrincipal.user_id`/`workspace_id` are resolved, never
  taken from the JWT directly:
  `JWT subject -> User.external_subject -> User -> WorkspaceMembership -> authorized workspace`.
  An unrecognized subject is `AuthenticationRequired`; a real user with no active membership
  in the requested workspace is `WorkspaceAccessDenied` — and that check never reveals
  whether the requested workspace even exists.
- **`WorkspaceMembership`** (`packages/domain`): minimal, not a full RBAC matrix — `role`
  (`owner`/`member`) x `status` (`active`/`inactive`). `promise_auth.authorization` maps role
  -> a small permission set (`commitments.read`/`.write`, `context.read`, `agent.execute`,
  `actions.approve`, `integrations.manage`); `require(principal, permission)` is what every
  route/tool calls before doing anything.
- **Errors** (`promise_shared.errors`): `AuthenticationRequired`/`InvalidToken`/`TokenExpired`
  -> REST `401` (with `WWW-Authenticate: Bearer`); `InsufficientScope`/`WorkspaceAccessDenied`
  -> REST `403`. A resource in a foreign workspace still `404`s via the existing
  `NotFoundError` path (workspace-scoped repository lookups), never `403` — so a request can't
  distinguish "wrong workspace" from "doesn't exist" by resource id alone.
- **MCP**: tools no longer accept `workspace_id`/`user_id`/`actor`/`decided_by` as arguments
  at all (see `services/mcp/promise_mcp/server.py`) — identity comes from
  `Context.headers["authorization"]`, the same Bearer-token surface Alexa+'s MCP
  account-linking (OAuth authorization code + PKCE) hands this adapter once linked. Switching
  `AUTH_MODE` to `oidc` and pointing `COGNITO_*` at the right user pool is the extension
  point; implementing Alexa+'s actual account-linking deployment configuration is out of
  scope for this milestone.
- **Web** (`web/lib/auth.ts`): every API call goes through `authHeaders()`.
  `NEXT_PUBLIC_AUTH_MODE=local` (default) sends `X-Dev-User-Id`/`X-Workspace-Id`, mirroring
  the API's local mode. `NEXT_PUBLIC_AUTH_MODE=oidc` sends a real `Authorization: Bearer`
  header once a session exists; `getAccessToken()` is the extension point a Cognito Hosted UI
  sign-in flow fills in — no sign-in UI is implemented yet.

```env
AUTH_MODE=local                      # or oidc
DEV_USER_ID=usr_dev_default          # AUTH_MODE=local only
DEV_WORKSPACE_ID=ws_dev_default      # AUTH_MODE=local only

COGNITO_ISSUER=https://cognito-idp.<region>.amazonaws.com/<user-pool-id>   # AUTH_MODE=oidc
COGNITO_AUDIENCE=<app-client-id>                                          # optional
COGNITO_JWKS_URL=                                                         # optional; OIDC discovery preferred
COGNITO_REQUIRED_SCOPES=                                                  # optional, comma-separated
```

**Try it**: `uv run pytest tests/test_auth_oidc.py tests/test_identity_resolution.py
tests/test_rest_auth.py tests/test_mcp_auth.py` — generated RSA keypair + self-signed JWT
fixtures, no live Cognito or network access required.

**Known limitations**: subject -> `User` resolution is a linear scan over `query_all("user")`
(see `identity.py`'s own docstring) — fine for local/demo data, but a real deployment needs a
proper index (e.g. a DynamoDB GSI on `external_subject`). No Cognito Hosted UI sign-in flow is
implemented on the web client yet — only the header/token plumbing. Alexa+'s actual
account-linking deployment configuration (redirect URIs, Cognito app client setup, etc.) is
not implemented — `_authenticate` in `services/mcp/promise_mcp/server.py` is the documented
extension point for it.

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
`packages/agent/promise_agent/commitment_extraction/provider.py` (commitment detection) both
call Bedrock only when `BEDROCK_ENABLED=true`; when it's unset/false, each uses its
deterministic mock directly (not as a failure fallback) — local dev and CI never require
live AWS credentials. With `BEDROCK_ENABLED=true`, a real Bedrock failure is never masked as
a mock result: both raise a classified, retryable `PromiseError` (`LLMProviderError` /
`ExtractionProviderError`) instead. See `infra/README.md` for deployment notes.

## Known limitations / next steps

- Identity & Authorization is v1 (see the section above): `AUTH_MODE=oidc` validates real
  Cognito/OIDC bearer tokens, but subject -> `User` resolution is a linear `query_all` scan
  (needs a real index at scale), there's no Cognito Hosted UI sign-in flow on the web client
  yet (only the header/token plumbing), and Alexa+'s actual account-linking deployment
  configuration isn't implemented — `_authenticate` in `services/mcp/promise_mcp/server.py`
  is the documented extension point for it.
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
