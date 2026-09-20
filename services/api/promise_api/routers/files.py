from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_domain.models import Contact

from ..deps import get_context, workspace_id

router = APIRouter(prefix="/api", tags=["files", "messages", "contacts"])


@router.get("/files")
def search_files(query: str = "", ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> list[dict[str, Any]]:
    return tools.search_files(ctx, workspace_id=ws, query=query)


@router.get("/files/{file_id}")
def get_file(file_id: str, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> dict[str, Any]:
    return tools.get_file(ctx, workspace_id=ws, file_id=file_id)


@router.get("/messages")
def search_messages(query: str = "", ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> list[dict[str, Any]]:
    return tools.search_messages(ctx, workspace_id=ws, query=query)


@router.get("/messages/{message_id}")
def get_message(message_id: str, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> dict[str, Any]:
    return tools.get_message(ctx, workspace_id=ws, message_id=message_id)


@router.get("/contacts")
def list_contacts(ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> list[Contact]:
    return tools.list_contacts(ctx, workspace_id=ws)
