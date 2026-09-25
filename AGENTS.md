## Product

PROMISE is a production-oriented AI follow-through agent.

Core flow:

Conversation → Commitment → Context → Plan → Approval → Action → Completion

Alexa+ is a channel, not the core product.

## Architecture

* Keep business logic in domain/application layers.
* REST and MCP are adapters.
* Keep integrations behind provider abstractions.
* UI must not access repositories/database directly.
* Avoid unnecessary microservices and speculative abstractions.

## Identity & Security

* AuthenticatedPrincipal is the source of truth.
* Never trust user_id/workspace_id from request bodies, query params, or MCP arguments.
* Enforce workspace and user ownership at application/repository boundaries.
* External side effects require explicit approval.
* Never log or commit tokens, secrets, or Authorization headers.
* Fail closed on ambiguous authorization.

## AI / Agent

* Separate extraction, retrieval, planning, approval, and execution.
* LLMs do not directly mutate databases or execute external actions.
* Real provider failures must not silently fall back to mocks.
* Use structured outputs when available.
* Unsupported actions must fail safely.

## Retrieval

Flow:

Commitment → ContextQuery → ContextRetriever → ContextItem[]

Keep retrieval deterministic and explainable.
Do not dump entire corpora into the LLM.
Do not add vector search without a concrete evaluated need.

## Integrations

Current important providers:

* Gmail
* Google Calendar

Keep OAuth, tokens, and provider APIs behind abstractions.

Never fabricate contact emails or external data.

## Documents

Keep:

content_text → AI/search

binary artifact → attachment/download

Never store large binaries in DynamoDB.
Use local blob storage for development and S3 for production.

## DynamoDB

Design from access patterns.

User-owned resources must use the appropriate indexed query path.
Do not scan an entire workspace and filter user_id in Python.

Keep DynamoDB-specific details below repository abstractions.

## MCP / MCP Apps

Keep MCP handlers thin.

MCP request
→ AuthenticatedPrincipal
→ Application service
→ structured result

Use the current official MCP Apps APIs.
Do not put product logic in the MCP UI.

## AWS

For AWS-specific work:

* use AWS Toolkit
* verify current AWS documentation
* prefer least-privilege IAM
* use Secrets Manager for production secrets

For version-sensitive libraries/APIs:

* use Context7
* inspect the installed/pinned version before changing APIs

## Development

Python uses uv.

```bash
uv sync
uv run pytest
uv run ruff check packages services tests
```

No requirements.txt or manual pip workflow.

## Before Coding

1. Inspect the existing implementation.
2. Check related tests.
3. Use Context7 for current library APIs.
4. Use AWS Toolkit/docs for AWS APIs.
5. Make the smallest correct change.
6. Run tests/lint/build.
7. Report actual results.

Do not redesign working architecture without a concrete reason.
