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
  - `OIDCAuthProvider` (`AUTH_MODE=oidc`): validates a `Bearer` token against JWKS —
    signature, issuer, expiration, `token_use`-correct audience, and required scopes.
    Compatible with Amazon Cognito User Pools and any standard OIDC provider. Never decodes a
    JWT without verifying its signature first, and never falls back to local identity on any
    failure — every failure raises a specific, classified error (see below).

    **Access vs. ID tokens, handled correctly**: a Cognito *access* token has a `client_id`
    claim and no `aud` claim at all; a Cognito *ID* token has the opposite. Asking PyJWT to
    verify `audience=<client_id>` on every token — the naive approach — rejects every real
    Cognito access token outright, since it then requires an `aud` claim access tokens never
    carry. `OIDCAuthProvider` decodes with `verify_aud=False` and checks the *correct* claim
    itself based on the token's own `token_use`: `client_id` for an access token (the
    default, preferred path — "prefer access tokens for API/MCP authorization"), `aud` for an
    ID token — and ID tokens are rejected outright unless `COGNITO_ALLOW_ID_TOKENS=true`
    (off by default; they also carry no OAuth `scope`, so a `required_scopes` check that
    matters will reject one anyway). `COGNITO_REQUIRED_SCOPES`, when set, requires **all**
    listed scopes to be present (not just one).
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
- **Resource ownership** (`packages/app/promise_app/tools.py`): workspace membership alone is
  not enough for a *personal* commitment resource — `get_commitment`, `update_commitment`,
  `complete_commitment`, `cancel_commitment`, and `handle_commitment` all additionally require
  `principal.user_id == commitment.user_id` (`WorkspaceAccessError`, 403, otherwise). `Action`/
  `Approval` carry no owner field of their own, so `decide_approval`/`execute_approved_action`
  derive ownership transitively through the commitment an action is for; `AgentRun` already
  has its own `user_id`, so `get_agent_run` checks that directly. `request_approval` (the
  manual, out-of-band MCP tool — distinct from the orchestrator's own internal call into the
  same approval step during `handle_commitment`, already gated by that function's own check)
  applies the identical transitive-ownership check before touching the target action.
  `IntegrationAccount` carries its own `user_id` (same shape as `Commitment`), so
  `disconnect_integration_account` requires `principal.user_id == account.user_id` directly.
- **List-level isolation** (same file): workspace membership is *also* not enough to **list**
  another user's personal resources — a leak the single-resource checks above didn't close.
  `search_commitments`/`list_integration_accounts` are scoped by both `workspace_id` and
  `user_id` via `Repository.list_user_owned` — the indexed path (the `UserOwnedIndex` GSI in
  production DynamoDB, a Query, never a Scan or a workspace-wide read filtered in Python; see
  "DynamoDB access patterns" below for the full design). `list_actions` and
  `list_pending_approvals` derive the caller's owned commitment/action ids with one bounded
  `Repository.list` join each (`_owned_commitment_ids`/`_owned_action_ids` — not N+1) and narrow
  to those; `list_actions(commitment_id=...)` additionally rejects an unowned commitment outright
  (`WorkspaceAccessError`), matching `get_commitment`'s single-resource behavior. `list_agent_runs`
  filters on `AgentRun.user_id` directly. `list_audit_events` stays workspace-scoped, deliberately:
  `AuditEvent.actor` can be a real user or `"system"`/`"agent"`, and a single event's
  `entity_type` spans commitments/actions/approvals with different owners — there's no one
  consistent "owner" to filter an audit log by; it's a workspace-level compliance log, not a
  personal resource (see that function's docstring for the full reasoning). No `list_drafts`
  application service exists yet — `Draft` has no owner field and nothing lists it today.
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
COGNITO_CLIENT_ID=<app-client-id>                                         # optional; checked against access tokens' client_id
COGNITO_JWKS_URL=                                                         # optional; OIDC discovery preferred
COGNITO_REQUIRED_SCOPES=                                                  # optional, comma-separated; ALL must be present
COGNITO_ALLOW_ID_TOKENS=false                                             # off by default -- access tokens preferred
```

**Try it**: `uv run pytest tests/test_auth_oidc.py tests/test_identity_resolution.py
tests/test_rest_auth.py tests/test_mcp_auth.py tests/test_resource_ownership.py` — generated
RSA keypair + self-signed JWT fixtures shaped like real Cognito access/ID tokens, no live
Cognito or network access required.

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
BLOB_STORE_BACKEND=s3          # document binary artifacts -- see "Document storage" below
SECRET_STORE_BACKEND=aws       # OAuth tokens -- see "Gmail Integration" below
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

**DynamoDB access patterns**: `Repository.list(workspace_id, ...)` reads every row of one
entity type in a workspace, then filters in Python (`EntityStore.query` — a DynamoDB Query on
`PK = WORKSPACE#<workspace_id>`, never a Scan, but still every row of that entity type, not
just the caller's own). That's fine for a workspace-wide resource, and fine at local/dev data
volumes for anything. It stops being fine for a *personal* resource at production scale: every
request would pay for every other user's rows too. Commitments and integration accounts are
exactly that shape (`Commitment.user_id` / `IntegrationAccount.user_id`), so their list access
pattern — `workspace_id + user_id` — gets an explicit, indexed path instead:
`Repository.list_user_owned(workspace_id, user_id, **filters)`, used by
`tools.search_commitments`/`tools.list_integration_accounts` and *only* those two. It queries
the `UserOwnedIndex` GSI (`GSI1PK = WORKSPACE#<workspace_id>#USER#<user_id>`, `GSI1SK =
<ENTITY>#<created_at>#<id>`) via `EntityStore.query_index` — a DynamoDB Query against
`IndexName`, narrowed to one entity type with a sort-key prefix, returned in chronological
order for free. **Ownership filtering is encoded in the index key itself, not applied as a
`FilterExpression` after the fact** — a `FilterExpression` still pays for reading every row
the Query's key condition matches before discarding the ones that fail the filter, so it would
silently defeat the whole point of adding a GSI here. `filters` (e.g. `status=`) are still
applied in Python, but only over the caller's own, already-narrow result set — never the
workspace's. `Repository.list()` itself is unchanged and still the right call for a genuinely
workspace-wide list (contacts, documents, audit events, ...); it never silently switches to an
indexed Query under the hood, so a caller's choice of `list()` vs. `list_user_owned()` always
matches what actually happens against the table. `Action`/`Approval`/`AgentRun` listing is
deliberately **not** given a GSI here: `Action`/`Approval` carry no owner field of their own
(ownership is transitive through `Action.commitment_id`, see "Resource ownership" above), so
indexing them this way would mean writing a denormalized owner attribute onto rows that don't
have one in the domain model just to make filtering easy — out of scope for "the smallest
clean production solution"; `list_agent_runs`'s workspace-scan-then-filter is accepted as-is
for now, a candidate for the same treatment later if it becomes a hot path, not because it's
architecturally different from the commitment case. `query_index` also accepts an optional
`limit` (pushed down to DynamoDB's own `Limit`, capping items *read* per page — the final
result can still come back shorter once `filters`/`include_deleted` apply after); there's no
pagination cursor, because nothing in this repository layer supports cursor-based pagination
yet for `query_index` to be consistent with. See `infra/README.md` for the GSI's table
definition, its production rollout path for an existing table with data in it already
(including `DynamoEntityStore.backfill_user_owned_index` — existing rows written before this
GSI existed need their `GSI1PK`/`GSI1SK` populated explicitly; DynamoDB's own online GSI
backfill only indexes rows that already carry those attributes), and
`packages/shared/promise_shared/store/index_keys.py` / `dynamodb_schema.py` for the code.
**A DynamoDB deployment is not indexing anything at production scale until `infra/deploy_gsi.py`
has actually been run against it** — declaring the GSI in code is not the same as it existing
on the live table.

## Gmail Integration

```
PROMISE
  |
  v
GmailIntegrationProvider (packages/integrations/promise_integrations/gmail/)
  |-- search_messages   (Gmail messages.list + per-id messages.get, format=full)
  |-- get_message
  \-- send_message      (Gmail messages.send -- RFC 2822 MIME, base64url raw field)
```

v1 is deliberately narrow: Gmail search/read (for Context Retrieval) and Gmail send (for the
approval-gated execution step) only. **No Google Drive, no Google Contacts, no Gmail Draft
API, no mailbox sync** — PROMISE's own `Draft`/`Action` model remains the one approval
boundary regardless of which provider ends up sending a message (see "Resource ownership"
above and `packages/agent/promise_agent/action_planning/send_message_planner.py`, which
always calls the *default* local provider's `create_draft`, never Gmail's).

**1. Create Google OAuth credentials.** In Google Cloud Console: create an OAuth 2.0 Client ID
   (Web application type), enable the Gmail API for the project, and add an OAuth consent
   screen requesting only the two scopes below.

**2. Required redirect URI**: exactly `GOOGLE_REDIRECT_URI` (below) — e.g.
   `http://localhost:8000/api/integrations/gmail/callback` for local dev,
   `https://<your-api-host>/api/integrations/gmail/callback` in production. Must match the URI
   registered on the OAuth client exactly (Google rejects a mismatch).

**3. Required scopes** (centralized in `promise_integrations/gmail/config.py`, never scattered
   across the codebase):
   ```
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/gmail.send
   ```
   Never `gmail.modify`/`gmail.compose`/`mail.google.com` — v1 has no reason to touch Gmail's
   own Draft API or otherwise mutate a mailbox beyond sending a message PROMISE's own approval
   flow already authorized. **Google's OAuth verification process**: requesting `gmail.send`
   (a "restricted" scope) from real end users requires completing Google's app verification
   (and, depending on usage, a security assessment) before the consent screen can serve
   external users at any real volume — this repository does not claim that verification is
   complete; it is a deployment prerequisite, not something `GMAIL_ENABLED=true` does for you.

**4. Local OAuth setup** — in `services/api/.env` (see `.env.example` for the full list):
   ```env
   GMAIL_ENABLED=true
   GOOGLE_CLIENT_ID=<your-client-id>
   GOOGLE_CLIENT_SECRET=<your-client-secret>
   GOOGLE_REDIRECT_URI=http://localhost:8000/api/integrations/gmail/callback
   WEB_APP_URL=http://localhost:3000
   SECRET_STORE_BACKEND=local   # dev/test only -- see "Token storage" below
   ```
   `GMAIL_ENABLED=false` (the default) means the feature doesn't exist at runtime at all —
   `IntegrationRegistry` never registers a Gmail provider factory, so every Gmail-aware call
   site (`resolve_for_user`) behaves exactly as it did before Gmail existed. Never commit real
   credentials — `.env` is gitignored; only `.env.example` (placeholders) is tracked.

**5. How to connect Gmail**: sign in to the web app, open **Connections**, click **Connect
   Gmail**. Mechanically: the client calls `GET /api/integrations/gmail/connect` (with the
   normal auth headers — `X-Dev-User-Id` locally / `Authorization: Bearer` in `oidc` mode) to
   get a Google authorization URL, then navigates the browser there itself (a plain link can't
   carry those headers on a top-level navigation, so this can't be a direct redirect from that
   endpoint). Google redirects back to `GET /api/integrations/gmail/callback` — a route with no
   PROMISE auth headers at all (top-level navigation from `accounts.google.com`); identity
   instead comes from the random, single-use, principal-bound `state` value the `/connect` call
   created (`promise_app/gmail_oauth.py`), never from a query parameter. On success it redirects
   to `{WEB_APP_URL}/connections?gmail=connected`; on any failure, `?gmail=error` — never a
   token in the URL either way.

**6. How PROMISE stores credentials securely**: `IntegrationAccount` (the same domain model
   every provider connection uses) never carries a raw token — only `secret_ref`, a pointer.
   The actual `{access_token, refresh_token, expires_at}` live in a separate `SecretStore`
   (`packages/shared/promise_shared/secrets/`): `SECRET_STORE_BACKEND=local` (dev/test) writes
   one file per secret, named by a hash of its ref, permissioned `0600`, under
   `LOCAL_SECRETS_DIR`; `SECRET_STORE_BACKEND=aws` (recommended for any real, `STORAGE_BACKEND=
   dynamodb` deployment) writes to AWS Secrets Manager, one secret named
   `<SECRETS_MANAGER_PREFIX><ref>` per account — no AWS account id/region/secret ARN hard-coded
   anywhere, only `AWS_REGION`/`SECRETS_MANAGER_PREFIX`. Access tokens refresh automatically
   (`GmailIntegrationProvider._access_token`, with a one-shot refresh-and-retry on an
   unexpected 401 too) using the stored refresh token; if Google reports the refresh token
   itself as revoked, the stored secret is deleted immediately (never retried) and the account
   needs to be reconnected.

**7. How Gmail is used in retrieval**: `GmailIntegrationProvider`/`MockGmailIntegrationProvider`
   are just another `ContextSearchProvider` (`MessageSearchProvider`, the same adapter class
   `LocalIntegrationProvider`'s messages already use) added to the list `ContextRetriever` fans
   out to — never a special code path inside `ContextRetriever`/`retriever.py` itself. Both
   `promise_agent.steps.retrieval.retrieve_context` (the orchestrator's own retrieval step, run
   during `handle_commitment`) and `tools.retrieve_commitment_context` (the on-demand endpoint)
   look up the commitment/request's own owning user's connected Gmail account
   (`IntegrationRegistry.resolve_for_user`) and add it to the provider list only when one
   exists; dedup is the existing `(type, source_id)` key `ContextRetriever` already uses. If
   Gmail isn't connected, retrieval runs exactly as it always has (local providers only, no
   fake Gmail results). If the Gmail provider itself fails mid-request, `ContextRetriever`'s
   existing per-provider error handling marks the retrieval `PARTIAL` (not `COMPLETE`) and
   keeps whatever the other providers found — never a fabricated result standing in for Gmail's.
   `tools.search_messages`/`get_message` (the generic message-search endpoints, used by both
   REST and the MCP `search_messages`/`get_message` tools) gain the same per-user Gmail
   participation automatically — no Gmail-specific code was added to either adapter, exactly as
   intended ("MCP tools should benefit automatically through the shared application layer").

**8. How Gmail sending is approval-gated**: identical to every other `SEND_MESSAGE` action —
   `Action` -> `WAITING_FOR_APPROVAL` -> explicit `decide_approval` -> `execute_approved_action`
   -> `completed`; `GmailIntegrationProvider` never sees an action before that state machine
   allows it to. The only thing Gmail changes is *who* performs the send: `execute_action`
   (`packages/agent/promise_agent/steps/execution.py`) resolves the acting user's own connected
   Gmail account and uses it if present, else falls back to the default (local) provider —
   never a redesign of the approval flow itself. Duplicate-send protection is two-layered: the
   existing `Action.status == EXECUTED` check (raises `DuplicateActionError` before Gmail is
   ever called again) and, since a `GmailIntegrationProvider` is constructed fresh per call
   rather than a long-lived singleton, a second guard keyed off the `Draft`'s own durable
   `status` (`SENT` -> treated as an idempotent replay, Gmail is never called again for it) —
   covers the "timed out after Gmail may have already accepted it" case specifically.
   When the `Draft` carries `attachment_document_id` (the planner's revised document; see
   `send_message_planner.py`), `build_raw_send_message`
   (`packages/integrations/promise_integrations/gmail/mime.py`) builds a `multipart/mixed`
   message — a `text/plain` body part plus the document's **binary artifact** as an attachment
   part (filename = `Document.name`, content type = `Document.type`, base64
   content-transfer-encoded, non-ASCII filenames RFC 2231-encoded automatically by Python's
   `email` package) — instead of a plain `text/plain` message; no attachment means no change
   from before. The document loaded is always the exact one the draft itself already
   references, never a caller-supplied id — `GmailIntegrationProvider.send_message` has no
   argument surface for reading an arbitrary document. **Never `Document.content_text`**: see
   "Document storage" below for why extracted text and the binary artifact are two separate
   things, and `DocumentArtifactMissing`/`AttachmentTooLarge` for the two ways attaching one
   fails safely instead of silently sending something wrong.

**Document storage**: a `Document` row carries two separate concerns, deliberately.
   `content_text` is *extracted* text — always present, used for retrieval/AI/search
   (`ContextRetriever`, `llm.revise_document`) — and is never what gets attached to an email.
   The binary artifact (the actual downloadable/attachable file) is separate: `storage_key`
   (reused, not a new field — see `Document`'s own docstring) is a pointer into a
   `DocumentBlobStore` (`packages/shared/promise_shared/blobs/`), keyed via
   `blob_ref(workspace_id, document_id)` so a reference always carries its own workspace
   scoping and one workspace's artifact can never collide with another's. `None` means no
   artifact exists (e.g. a text-only document) — `artifact_size_bytes` and the existing
   `name`/`type` fields double as the artifact's own size/filename/content-type when one does,
   so no separate `artifact_filename`/`artifact_content_type` fields were needed.
   `LocalBlobStore` (dev/test — `BLOB_STORE_BACKEND=local`, the default) stores one directory
   per reference under `LOCAL_DOCUMENTS_DIR`, named by a hash of the reference (never the
   reference/filename verbatim — path traversal is structurally impossible, not just
   validated against). `S3BlobStore` (production — `BLOB_STORE_BACKEND=s3`) is the same
   interface against `S3_BUCKET`, private objects only, no bucket/region/key hard-coded.
   DynamoDB/local JSON only ever stores the `storage_key` pointer and size, never the bytes.

   When `send_message_planner.py` revises a document, it generates a **real** binary artifact
   matching the source format — a real DOCX via `python-docx`
   (`packages/agent/promise_agent/action_planning/document_artifacts.py`) when the source is
   DOCX (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`; the seeded
   demo proposal is one), or the revised text's own UTF-8 bytes under its own real content type
   otherwise — text content already *is* its own valid binary representation, so that's not a
   workaround; it never labels plain text as DOCX/PDF, and never writes text into a file with a
   misleading extension. `GmailIntegrationProvider.send_message` loads the artifact through
   `DocumentBlobStore` and refuses, rather than substitutes, when something's wrong: no artifact
   at all raises `DocumentArtifactMissing` (never a silent fall back to `content_text`); an
   artifact over `GMAIL_MAX_ATTACHMENT_BYTES` (default 25 MiB — Gmail's own documented
   `messages.send` limit) raises `AttachmentTooLarge` before any MIME message is built or Gmail
   is called at all. A development-only download route exists too:
   `GET /api/documents/{document_id}/artifact` (`tools.get_document_artifact`) — workspace-scoped
   the same way `get_file` is (`Document` has no per-user ownership; see "Resource ownership"
   above), never exposing a raw storage key/path to the client.

   Supported artifact formats today: DOCX (generated) and plain text (always, as the
   fallback/default). PDF generation is not implemented — the Gmail attachment pipeline itself
   (MIME building, blob storage, size limits) is format-agnostic and handles any binary content
   correctly regardless of format (see `tests/test_gmail_mime_fixtures.py`, which proves this
   against a real minimal PDF fixture), but nothing in the revision engine *produces* a PDF yet.

**9. Current limitation**: v1 always searches Gmail live, per request — there is no mailbox
   sync/indexing subsystem, and nothing about a connected mailbox is copied into
   DynamoDB/local JSON beyond the OAuth tokens themselves (data minimization, by design — see
   `GmailIntegrationProvider`'s own docstring). This means retrieval latency includes a live
   Gmail API round trip whenever a connected account participates, and there's no way to search
   Gmail content PROMISE hasn't been asked to search in the current request.

**10. Future**: an incremental sync/indexing layer (e.g. periodically pulling and caching
   recent messages, using Gmail's `historyId`/`users.history.list` for incremental updates) is
   a natural next step if live-per-request latency becomes a problem — `ContextSearchProvider`
   is exactly the seam it would plug into, with no changes to `ContextRetriever` or its callers,
   the same way Gmail itself plugged in.

**Try it**: `uv run pytest tests/test_gmail_mime.py tests/test_gmail_mime_fixtures.py
tests/test_gmail_oauth_client.py tests/test_gmail_provider.py tests/test_gmail_mock_provider.py
tests/test_gmail_oauth_flow.py tests/test_gmail_oauth_routes.py tests/test_gmail_context_retrieval.py
tests/test_gmail_authorization.py tests/test_gmail_approval_flow.py tests/test_secret_store.py
tests/test_document_blob_store.py tests/test_document_artifact.py`
— `httpx.MockTransport`-backed fakes for every Google HTTP call, a hand-written fake S3 client
for `S3BlobStore`, and real (fixture) `.docx`/`.pdf` binaries under `tests/fixtures/` — no live
network or real Google/AWS credentials required.

## Known limitations / next steps

- Identity & Authorization is v1 (see the section above): `AUTH_MODE=oidc` validates real
  Cognito/OIDC bearer tokens, but subject -> `User` resolution is a linear `query_all` scan
  (needs a real index at scale), there's no Cognito Hosted UI sign-in flow on the web client
  yet (only the header/token plumbing), and Alexa+'s actual account-linking deployment
  configuration isn't implemented — `_authenticate` in `services/mcp/promise_mcp/server.py`
  is the documented extension point for it.
- Two integration providers exist now: `LocalIntegrationProvider` (demo/local data, still the
  default/workspace-wide one) and `GmailIntegrationProvider` (see "Gmail Integration" above —
  messages only: search/read/send, no Drive/Contacts/mailbox sync). Drive, Slack, etc. remain
  additive the same way Gmail was: implement `IntegrationProvider` and register a factory — no
  changes needed in `packages/agent`/`packages/app`. Gmail's own OAuth scopes have not gone
  through Google's app verification process (see "Gmail Integration" point 3) — that's a real
  deployment prerequisite for serving external users, not something this repository does.
- `IntegrationAccount.secret_ref` now points into a real `SecretStore` (local file store for
  dev/test, AWS Secrets Manager in production — see "Gmail Integration" point 6) for Gmail.
  Other future providers' credentials would use the same seam.
- `Document.storage_key` now points into a real `DocumentBlobStore` (local filesystem for
  dev/test, S3 in production — see "Document storage" above). Only DOCX and plain text have a
  real artifact *generator* (`document_artifacts.py`); PDF generation isn't implemented, even
  though the attachment/blob-storage pipeline itself already handles PDF (or any other binary
  format) correctly once bytes exist for it — see that section's own note.
- Context retrieval (`packages/agent/promise_agent/context_retrieval/`) is v1: deterministic
  lexical/keyword + metadata ranking, no semantic/vector search yet. The `ContextSearchProvider`
  and `RankingStrategy` seams are shaped for that to be a later addition without
  `ContextRetriever` or its callers changing — see the Context Retrieval Engine section above.
- DynamoDB adapter is a straightforward single-table CRUD implementation; it has not been
  load-tested and doesn't yet use a GSI for `query_all` (admin/maintenance path only).
- The web UI still has the original single-page demo flow; approvals/activity/connections
  screens described in the target architecture are exposed via REST but not yet all
  represented in dedicated web screens.
