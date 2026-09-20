from __future__ import annotations

from promise_domain.enums import CommitmentStatus
from promise_domain.models import Commitment
from promise_shared.clock import iso_now

from ..context import AgentRepos


def complete_commitment(commitment_id: str, workspace_id: str, repos: AgentRepos) -> Commitment:
    return repos.commitments.update(
        workspace_id,
        commitment_id,
        lambda c: (setattr(c, "status", CommitmentStatus.COMPLETED), setattr(c, "completed_at", iso_now())),
    )


def cancel_commitment(commitment_id: str, workspace_id: str, repos: AgentRepos) -> Commitment:
    return repos.commitments.update(
        workspace_id,
        commitment_id,
        lambda c: (setattr(c, "status", CommitmentStatus.CANCELLED), setattr(c, "cancelled_at", iso_now())),
    )
