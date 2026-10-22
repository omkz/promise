from __future__ import annotations

from typing import TYPE_CHECKING, Any

from strands import tool

if TYPE_CHECKING:
    from promise_app.bootstrap import AppContext


def build_strands_tools(ctx: AppContext, *, workspace_id: str, user_id: str) -> list[Any]:
    """Build explicitly validated PROMISE tools for Strands agents.

    Security & Safety Guarantees:
    - Binds workspace_id and user_id in closures from the authenticated caller.
      The model cannot forge, tamper with, or access another workspace or user.
    - Exposes only safe, validated operations through existing application services.
    - NO unrestricted database, filesystem, or arbitrary Python access is granted.
    - CRITICAL: NO approval or execution tools (decide_approval, execute_approved_action)
      are exposed. Models can propose actions through `handle_commitment`, but CANNOT
      approve or execute them. All external side effects require human approval.
    """
    from promise_app import tools as app_tools

    @tool
    def create_commitment(text: str) -> dict[str, Any]:
        """Detect and remember a personal commitment from text.

        Args:
            text: The commitment text or utterance (e.g. "I'll send Andi the revised proposal tomorrow morning.").
        """
        clean = text.strip()
        if not clean:
            return {"detected": False, "error": "Commitment text cannot be empty."}

        res = app_tools.create_commitment(
            ctx,
            workspace_id=workspace_id,
            user_id=user_id,
            text=clean,
            source_system="strands_agent",
        )
        com = res.get("commitment")
        return {
            "detected": res.get("detected", False),
            "persisted": res.get("persisted", False),
            "commitment_id": com.id if hasattr(com, "id") else (com.get("id") if isinstance(com, dict) else None),
            "title": com.title if hasattr(com, "title") else (com.get("title") if isinstance(com, dict) else None),
            "status": com.status.value if hasattr(com, "status") and hasattr(com.status, "value") else str(getattr(com, "status", "")),
            "confidence": getattr(com, "confidence", None),
            "reason": res.get("reason"),
        }

    @tool
    def list_commitments(status: str | None = None) -> list[dict[str, Any]]:
        """List commitments belonging to the authenticated user.

        Args:
            status: Optional status filter (e.g. "open", "in_progress", "waiting_for_approval", "completed").
        """
        commitments = app_tools.search_commitments(ctx, workspace_id=workspace_id, user_id=user_id, status=status)
        return [
            {
                "id": c.id,
                "title": c.title,
                "description": c.description,
                "due_at": c.due_at,
                "status": c.status.value,
                "priority": c.priority.value,
                "confidence": c.confidence,
            }
            for c in commitments
        ]

    @tool
    def get_commitment(commitment_id: str) -> dict[str, Any]:
        """Get details of a specific commitment by its ID.

        Args:
            commitment_id: The ID of the commitment to inspect.
        """
        c = app_tools.get_commitment(ctx, workspace_id=workspace_id, user_id=user_id, commitment_id=commitment_id)
        return {
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "due_at": c.due_at,
            "status": c.status.value,
            "priority": c.priority.value,
            "contact_id": c.contact_id,
            "confidence": c.confidence,
        }

    @tool
    def handle_commitment(commitment_id: str) -> dict[str, Any]:
        """Trigger PROMISE context retrieval and action planning for a commitment.

        This retrieves relevant documents/messages, plans the appropriate action,
        and requests user approval. The proposed action enters 'waiting_for_approval'.
        NOTE: This tool NEVER executes external actions; execution requires explicit
        human approval.

        Args:
            commitment_id: The ID of the commitment to handle.
        """
        res = app_tools.handle_commitment(
            ctx,
            workspace_id=workspace_id,
            user_id=user_id,
            commitment_id=commitment_id,
            trigger="strands_agent",
        )
        action = res["action"]
        approval = res["approval"]
        return {
            "agent_run_id": res["agent_run"].id,
            "action_id": action.id,
            "action_type": action.type.value,
            "action_status": action.status.value,
            "approval_id": approval.id,
            "approval_status": approval.status.value,
            "notice": "Action proposed successfully and waiting for user approval. Models cannot self-approve.",
        }

    @tool
    def list_pending_approvals() -> list[dict[str, Any]]:
        """List approvals awaiting human decision for the authenticated user."""
        approvals = app_tools.list_pending_approvals(ctx, workspace_id=workspace_id, user_id=user_id)
        return [
            {
                "id": a.id,
                "action_id": a.action_id,
                "status": a.status.value,
                "created_at": a.created_at,
            }
            for a in approvals
        ]

    @tool
    def get_action(action_id: str) -> dict[str, Any]:
        """Get details and status of an action by its ID.

        Args:
            action_id: The ID of the action.
        """
        a = app_tools.get_action(ctx, workspace_id=workspace_id, user_id=user_id, action_id=action_id)
        return {
            "id": a.id,
            "type": a.type.value,
            "status": a.status.value,
            "commitment_id": a.commitment_id,
            "payload": a.payload,
            "error": a.error,
        }

    @tool
    def list_contacts() -> list[dict[str, Any]]:
        """List contacts available in the workspace."""
        contacts = app_tools.list_contacts(ctx, workspace_id=workspace_id, user_id=user_id)
        return [{"id": c.id, "name": c.name, "email": c.email} for c in contacts]

    @tool
    def list_documents(query: str = "") -> list[dict[str, Any]]:
        """List context documents available in the workspace.

        Args:
            query: Optional search keyword for document filenames or content.
        """
        docs = app_tools.search_files(ctx, workspace_id=workspace_id, query=query)
        return [{"id": d.get("id"), "name": d.get("name"), "type": d.get("type")} for d in docs]

    return [
        create_commitment,
        list_commitments,
        get_commitment,
        handle_commitment,
        list_pending_approvals,
        get_action,
        list_contacts,
        list_documents,
    ]
