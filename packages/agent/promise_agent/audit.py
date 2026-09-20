from __future__ import annotations

from promise_domain.models import AuditEvent
from promise_shared.ids import new_id

from .context import AgentRepos


def record(
    repos: AgentRepos,
    workspace_id: str,
    *,
    actor: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    summary: str,
    agent_run_id: str | None = None,
    action_id: str | None = None,
    approval_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        id=new_id("aud"),
        workspace_id=workspace_id,
        actor=actor,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        agent_run_id=agent_run_id,
        action_id=action_id,
        approval_id=approval_id,
        summary=summary,
    )
    return repos.audit_events.save(event)
