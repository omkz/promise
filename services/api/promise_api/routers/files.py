from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_auth import AuthenticatedPrincipal, Permission, require
from promise_domain.models import Contact

from ..deps import get_context, get_principal

router = APIRouter(prefix="/api", tags=["files", "messages", "contacts"])


@router.get("/files")
def search_files(
    query: str = "", principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> list[dict[str, Any]]:
    require(principal, Permission.CONTEXT_READ)
    return tools.search_files(ctx, workspace_id=principal.workspace_id, query=query)


@router.get("/files/{file_id}")
def get_file(
    file_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    require(principal, Permission.CONTEXT_READ)
    return tools.get_file(ctx, workspace_id=principal.workspace_id, file_id=file_id)


@router.get("/messages")
def search_messages(
    query: str = "", principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> list[dict[str, Any]]:
    require(principal, Permission.CONTEXT_READ)
    return tools.search_messages(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, query=query)


@router.get("/messages/{message_id}")
def get_message(
    message_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    require(principal, Permission.CONTEXT_READ)
    return tools.get_message(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, message_id=message_id)


@router.get("/contacts")
def list_contacts(
    principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> list[Contact]:
    require(principal, Permission.CONTEXT_READ)
    return tools.list_contacts(ctx, workspace_id=principal.workspace_id)
