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
    ConflictError,
    DuplicateActionError,
    NotFoundError,
    PromiseError,
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


@app.exception_handler(ApprovalRequiredError)
def _approval_required(_: Request, exc: ApprovalRequiredError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=409)


@app.exception_handler(DuplicateActionError)
def _duplicate_action(_: Request, exc: DuplicateActionError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=409)


@app.exception_handler(ConflictError)
def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=409)


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
