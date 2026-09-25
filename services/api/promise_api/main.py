from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from promise_app.bootstrap import AppContext
from promise_integrations.calendar import CalendarNotConfigured
from promise_integrations.gmail import GmailNotConfigured
from promise_shared.errors import (
    ApprovalRequiredError,
    AttachmentTooLarge,
    AuthenticationRequired,
    ConflictError,
    DocumentArtifactMissing,
    DuplicateActionError,
    ExtractionProviderError,
    InsufficientScope,
    IntegrationAuthorizationRequired,
    IntegrationAuthorizationRevoked,
    IntegrationConflict,
    IntegrationInvalidRequest,
    IntegrationNotConnected,
    IntegrationNotFound,
    IntegrationPermissionDenied,
    IntegrationRateLimited,
    IntegrationTokenExpired,
    IntegrationUnavailable,
    InvalidToken,
    LLMProviderError,
    NotFoundError,
    PromiseError,
    TokenExpired,
    WorkspaceAccessDenied,
    WorkspaceAccessError,
)

from .deps import get_context
from .routers import agent, commitments, files, integrations

app = FastAPI(title="PROMISE API", version="0.1.0")

allowed_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins or ["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(NotFoundError)
def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=404)


@app.exception_handler(WorkspaceAccessError)
def _forbidden(_: Request, exc: WorkspaceAccessError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=403)


# ---- identity & authorization (see promise_auth / promise_app.identity) --------------------
#
# 401: the caller isn't authenticated at all, or their token is unusable. 403: the caller IS
# authenticated but isn't authorized for this workspace/action. Neither handler below ever
# echoes the Authorization header or the token itself — `str(exc)` only ever carries a short,
# pre-written reason (see promise_shared.errors), never raw credentials.


@app.exception_handler(AuthenticationRequired)
def _authentication_required(_: Request, exc: AuthenticationRequired) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


@app.exception_handler(InvalidToken)
def _invalid_token(_: Request, exc: InvalidToken) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


@app.exception_handler(TokenExpired)
def _token_expired(_: Request, exc: TokenExpired) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


@app.exception_handler(InsufficientScope)
def _insufficient_scope(_: Request, exc: InsufficientScope) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=403)


@app.exception_handler(WorkspaceAccessDenied)
def _workspace_access_denied(_: Request, exc: WorkspaceAccessDenied) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=403)


@app.exception_handler(ApprovalRequiredError)
def _approval_required(_: Request, exc: ApprovalRequiredError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=409)


@app.exception_handler(DuplicateActionError)
def _duplicate_action(_: Request, exc: DuplicateActionError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=409)


@app.exception_handler(ConflictError)
def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=409)


@app.exception_handler(ExtractionProviderError)
def _extraction_provider_error(_: Request, exc: ExtractionProviderError) -> JSONResponse:
    """A configured extraction provider (Bedrock) failed. Never masked as a mock
    result — surfaced as a controlled, classified error the caller can act on."""
    return JSONResponse(
        {"error": str(exc), "provider": exc.provider_name, "retryable": exc.retryable}, status_code=503
    )


@app.exception_handler(LLMProviderError)
def _llm_provider_error(_: Request, exc: LLMProviderError) -> JSONResponse:
    """A configured LLM provider (Bedrock, document revision) failed. Never masked
    as a mock result — surfaced as a controlled, classified error the caller can act on."""
    return JSONResponse(
        {"error": str(exc), "provider": exc.provider_name, "retryable": exc.retryable}, status_code=503
    )


# ---- external integration providers (Gmail, ...) -------------------------------------------
#
# Never echoes a raw provider response body, an OAuth token, or an Authorization header --
# `str(exc)` only ever carries the short, fixed detail strings built in promise_shared.errors.


@app.exception_handler(IntegrationNotConnected)
def _integration_not_connected(_: Request, exc: IntegrationNotConnected) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=409)


@app.exception_handler(IntegrationAuthorizationRequired)
def _integration_authorization_required(_: Request, exc: IntegrationAuthorizationRequired) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=401)


@app.exception_handler(IntegrationTokenExpired)
def _integration_token_expired(_: Request, exc: IntegrationTokenExpired) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=401)


@app.exception_handler(IntegrationAuthorizationRevoked)
def _integration_authorization_revoked(_: Request, exc: IntegrationAuthorizationRevoked) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=401)


@app.exception_handler(IntegrationPermissionDenied)
def _integration_permission_denied(_: Request, exc: IntegrationPermissionDenied) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=403)


@app.exception_handler(IntegrationRateLimited)
def _integration_rate_limited(_: Request, exc: IntegrationRateLimited) -> JSONResponse:
    headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else {}
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=429, headers=headers)


@app.exception_handler(IntegrationUnavailable)
def _integration_unavailable(_: Request, exc: IntegrationUnavailable) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=503)


@app.exception_handler(IntegrationInvalidRequest)
def _integration_invalid_request(_: Request, exc: IntegrationInvalidRequest) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=400)


@app.exception_handler(DocumentArtifactMissing)
def _document_artifact_missing(_: Request, exc: DocumentArtifactMissing) -> JSONResponse:
    return JSONResponse({"error": str(exc), "document_id": exc.document_id}, status_code=409)


@app.exception_handler(AttachmentTooLarge)
def _attachment_too_large(_: Request, exc: AttachmentTooLarge) -> JSONResponse:
    return JSONResponse(
        {"error": str(exc), "document_id": exc.document_id, "size_bytes": exc.size_bytes, "max_bytes": exc.max_bytes},
        status_code=413,
    )


@app.exception_handler(IntegrationNotFound)
def _integration_not_found(_: Request, exc: IntegrationNotFound) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=404)


@app.exception_handler(IntegrationConflict)
def _integration_conflict(_: Request, exc: IntegrationConflict) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": exc.provider_name}, status_code=409)


@app.exception_handler(GmailNotConfigured)
def _gmail_not_configured(_: Request, exc: GmailNotConfigured) -> JSONResponse:
    """An admin/deployment configuration gap (missing GOOGLE_CLIENT_ID/SECRET/
    REDIRECT_URI), not a per-user "not connected" state -- 503, matching how
    every other unconfigured/unavailable provider dependency is surfaced."""
    return JSONResponse({"error": str(exc), "provider": "gmail"}, status_code=503)


@app.exception_handler(CalendarNotConfigured)
def _calendar_not_configured(_: Request, exc: CalendarNotConfigured) -> JSONResponse:
    return JSONResponse({"error": str(exc), "provider": "google_calendar"}, status_code=503)


@app.exception_handler(ValueError)
def _bad_request(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(PromiseError)
def _unprocessable(_: Request, exc: PromiseError) -> JSONResponse:
    """Fallback for any domain/application error without a more specific handler above
    (e.g. planning failures like "no relevant document found")."""
    return JSONResponse({"error": str(exc)}, status_code=422)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "promise-api"}


@app.get("/ready")
def ready(ctx: AppContext = Depends(get_context)) -> dict:
    ctx.repos.workspaces.get(ctx.default_workspace_id)  # touches storage
    return {"status": "ready"}


app.include_router(commitments.router)
app.include_router(files.router)
app.include_router(agent.router)
app.include_router(integrations.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
