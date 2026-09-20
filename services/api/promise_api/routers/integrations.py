from __future__ import annotations

from fastapi import APIRouter, Depends
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_domain.models import IntegrationAccount
from pydantic import BaseModel

from ..deps import get_context, user_id, workspace_id

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class ConnectIntegrationBody(BaseModel):
    provider: str
    account_identifier: str
    scopes: list[str] = []


@router.get("")
def list_integrations(ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> list[IntegrationAccount]:
    return tools.list_integration_accounts(ctx, workspace_id=ws)


@router.post("", status_code=201)
def connect_integration(
    body: ConnectIntegrationBody, ws: str = Depends(workspace_id), uid: str = Depends(user_id), ctx: AppContext = Depends(get_context)
) -> IntegrationAccount:
    return tools.connect_integration_account(
        ctx, workspace_id=ws, user_id=uid, provider=body.provider, account_identifier=body.account_identifier, scopes=body.scopes
    )


@router.post("/{account_id}/disconnect")
def disconnect_integration(account_id: str, ws: str = Depends(workspace_id), ctx: AppContext = Depends(get_context)) -> IntegrationAccount:
    return tools.disconnect_integration_account(ctx, workspace_id=ws, account_id=account_id)
