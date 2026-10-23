# PROMISE

PROMISE is a production-oriented AI follow-through agent. It captures commitments from conversations, retrieves relevant context, proposes concrete actions, requires human approval, and safely executes external side effects (Gmail, Google Calendar).

**Core Loop:**
`Conversation → Commitment → Context → Plan → Approval → Action → Completion`

Alexa+ and the web dashboard are channels into PROMISE. All business logic lives in channel-agnostic application services called identically by REST and MCP.

---

## Architecture

```
apps/web               Next.js dashboard (browser client)
services/api           FastAPI REST API
services/mcp           MCP Streamable HTTP adapter (Alexa+ / MCP clients)
packages/domain        Entities, statuses, workspace repositories
packages/agent         Extraction, retrieval, planning, execution, approval
packages/integrations  Integration abstractions (Gmail, Google Calendar, Local)
packages/app           Application layer (promise_app.tools)
packages/auth          JWT/JWKS validation, principal resolution
packages/shared        Storage adapters (JSON, DynamoDB), clock, errors
```

Neither `services/api` nor `services/mcp` contains business logic — both are thin transport adapters over `packages/app/promise_app/tools.py`.

---

## Quickstart

### Prerequisites
- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node.js 18+

### Setup & Run Services

```bash
# 1. Install Python dependencies
uv sync

# 2. Seed development workspace
uv run python -m promise_app.seed

# 3. Start REST API (port 8000)
(cd services/api && uv run uvicorn promise_api.main:app --reload --port 8000)

# 4. Start MCP Server (port 8001, optional)
(cd services/mcp && uv run uvicorn promise_mcp.server:app --reload --port 8001)

# 5. Start Web Dashboard (port 3000)
cd web
npm install
cp .env.local.example .env.local
npm run dev
```

### Docker Alternative

```bash
docker compose up --build
```

Runs API (`:8000`), MCP (`:8001`), and Web (`:3000`) sharing `./data`.

---

## Testing & Quality

```bash
uv run pytest                                # Run full test suite (600+ tests)
uv run ruff check packages services tests    # Linting
```

---

## Web Dashboard

Available routes on `http://localhost:3000`:
- `/`: Commitments overview & quick creation
- `/approvals`: Pending approval review and action decisions
- `/activity`: Action execution history and agent runs (sorted newest first)
- `/connections`: Connect Gmail & Google Calendar accounts
- `/alexa`: Interactive Alexa+ voice and Commitment Card simulator

---

## Core Safety & Governance

1. **Mandatory Human Approval**:
   - External side effects (sending emails, creating calendar events) never execute automatically.
   - Proposed actions enter `WAITING_FOR_APPROVAL` with a pending `Approval`.
   - The user must explicitly approve via UI or `POST /api/approvals/{id}/decide`.

2. **Reject Safety**:
   - Rejecting an approval transitions both `Approval` and `Action` to `REJECTED`.
   - Both states are terminal; repeat decisions raise `ConflictError`.
   - Attempting to execute a rejected action raises `ApprovalRequiredError` without calling external providers.

3. **Concurrency-Safe Idempotency**:
   - **Application claim**: `execute_action` uses an atomic `APPROVED -> EXECUTING` CAS claim. If multiple requests race, exactly one caller proceeds; others fail with `DuplicateActionError`.
   - **Post-execution**: already executed (`EXECUTED`) actions reject subsequent calls immediately.
   - **Google Calendar provider**: uses deterministic SHA-1 event IDs (`Action.idempotency_key`). Repeated inserts return HTTP 409, handled as an idempotent replay (`idempotent_replay: True`) rather than creating duplicates.
   - **Gmail provider**: uses atomic draft status claims (`DRAFT -> SENDING`) before sending via Gmail API.

4. **Deterministic Context Retrieval**:
   - Retrieval matches lexical/keyword and metadata criteria (contacts, dates, thread history).
   - No opaque vector embeddings or unverified data dumped into LLM prompts.

---

## Integrations

- **Gmail**: OAuth2 flow (`/api/integrations/gmail/connect`), token refresh, search messages, send drafts.
- **Google Calendar**: OAuth2 flow (`/api/integrations/calendar/connect`), event search, event creation on `primary` calendar.
- **Strands Agents (Optional)**: Conversational orchestration using foundation models (Bedrock, Gemini, Groq) via Strands Agents SDK. Bound to PROMISE tools; cannot self-approve external actions.

---

## Configuration & Environments

Key environment variables:
- `PROMISE_ENV`: `development` (local file store) or `production` (DynamoDB + S3 + Secrets Manager).
- `AUTH_MODE`: `development` (mock principal) or `oidc` (Cognito / JWT bearer verification).
- `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET`: Google OAuth credentials.
- `CALENDAR_CLIENT_ID` / `CALENDAR_CLIENT_SECRET`: Google Calendar credentials.
- `STRANDS_ENABLED`: Set to `true` to enable conversational AI panel.
