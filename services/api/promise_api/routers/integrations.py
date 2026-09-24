from __future__ import annotations

from fastapi import APIRouter, Depends
from promise_app import tools
from promise_app.bootstrap import AppContext
from promise_auth import AuthenticatedPrincipal, Permission, require
from promise_domain.models import IntegrationAccount
from pydantic import BaseModel

from ..deps import get_context, get_principal

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class ConnectIntegrationBody(BaseModel):
    provider: str
    account_identifier: str
    scopes: list[str] = []


@router.get("")
def list_integrations(
    principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> list[IntegrationAccount]:
    require(principal, Permission.INTEGRATIONS_MANAGE)
    return tools.list_integration_accounts(ctx, workspace_id=principal.workspace_id, user_id=principal.user_id)


@router.post("", status_code=201)
def connect_integration(
    body: ConnectIntegrationBody, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> IntegrationAccount:
    require(principal, Permission.INTEGRATIONS_MANAGE)
    return tools.connect_integration_account(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, provider=body.provider,
        account_identifier=body.account_identifier, scopes=body.scopes,
    )


@router.post("/{account_id}/disconnect")
def disconnect_integration(
    account_id: str, principal: AuthenticatedPrincipal = Depends(get_principal), ctx: AppContext = Depends(get_context)
) -> IntegrationAccount:
    require(principal, Permission.INTEGRATIONS_MANAGE)
    return tools.disconnect_integration_account(
        ctx, workspace_id=principal.workspace_id, user_id=principal.user_id, account_id=account_id
    )
