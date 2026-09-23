from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from promise_app.bootstrap import AppContext
from promise_shared.errors import (
    ApprovalRequiredError,
    AuthenticationRequired,
    ConflictError,
    DuplicateActionError,
    ExtractionProviderError,
    InsufficientScope,
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
