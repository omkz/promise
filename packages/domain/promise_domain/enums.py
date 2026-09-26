from __future__ import annotations

from enum import Enum


class CommitmentStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_EXTERNAL_DEPENDENCY = "waiting_for_external_dependency"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    OVERDUE = "overdue"


CLOSED_COMMITMENT_STATUSES = {CommitmentStatus.COMPLETED, CommitmentStatus.CANCELLED, CommitmentStatus.FAILED}


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionType(str, Enum):
    CREATE_DRAFT = "create_draft"
    SEND_MESSAGE = "send_message"
    PUBLISH = "publish"
    MODIFY_DOCUMENT = "modify_document"
    DELETE = "delete"
    CREATE_CALENDAR_EVENT = "create_calendar_event"


class ActionStatus(str, Enum):
    PROPOSED = "proposed"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTION_TYPES_REQUIRING_APPROVAL = {
    ActionType.SEND_MESSAGE,
    ActionType.PUBLISH,
    ActionType.MODIFY_DOCUMENT,
    ActionType.DELETE,
    ActionType.CREATE_CALENDAR_EVENT,
}


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DraftStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    # Claimed by exactly one send_message call (atomic DRAFT -> SENDING compare-and-set,
    # see GmailIntegrationProvider.send_message) between "about to call the provider's
    # send API" and "provider confirmed it sent" -- mirrors ActionStatus.EXECUTING one
    # layer down, so a second concurrent send_message for the same draft never both send.
    SENDING = "sending"
    SENT = "sent"


class IntegrationStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    PENDING = "pending"


class AgentRunStatus(str, Enum):
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentStepName(str, Enum):
    EXTRACTION = "extraction"
    RETRIEVAL = "retrieval"
    PLANNING = "planning"
    EXECUTION = "execution"
    APPROVAL = "approval"
    COMPLETION = "completion"


class AgentStepStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class MembershipRole(str, Enum):
    """Deliberately minimal — see WorkspaceMembership. Not a full RBAC matrix."""

    OWNER = "owner"
    MEMBER = "member"


class MembershipStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
