from __future__ import annotations

from contextlib import contextmanager

from promise_domain.enums import AgentRunStatus, AgentStepName, AgentStepStatus, CommitmentStatus
from promise_domain.models import AgentRun, AgentStep
from promise_shared.clock import iso_now
from promise_shared.ids import new_id

from . import audit
from .action_planning import select_planner
from .context import AgentRepos
from .steps import approval as approval_step
from .steps import completion as completion_step
from .steps import execution as execution_step
from .steps import retrieval as retrieval_step


class AgentOrchestrator:
    """Runs the PROMISE agent loop: retrieve -> plan -> propose -> (wait) -> execute -> complete.

    Every step is recorded as an AgentStep under an AgentRun, and every
    state change is mirrored to the audit log. This class contains *no*
    channel-specific logic — REST and MCP both call the same orchestrator
    through `promise_app`.
    """

    def __init__(self, repos: AgentRepos) -> None:
        self.repos = repos

    @contextmanager
    def _step(self, agent_run: AgentRun, name: AgentStepName, tool_calls: list[str]):
        step = AgentStep(id=new_id("step"), workspace_id=agent_run.workspace_id, agent_run_id=agent_run.id, name=name, tool_calls=tool_calls)
        self.repos.agent_steps.save(step)
        try:
            yield step
        except Exception as exc:  # noqa: BLE001
            step.status = AgentStepStatus.FAILED
            step.error = str(exc)
            step.ended_at = iso_now()
            self.repos.agent_steps.save(step)
            raise
        else:
            if step.status == AgentStepStatus.RUNNING:
                step.status = AgentStepStatus.COMPLETED
            step.ended_at = iso_now()
            self.repos.agent_steps.save(step)

    def run_handle_commitment(self, workspace_id: str, user_id: str, commitment_id: str, *, trigger: str) -> dict:
        commitment = self.repos.commitments.require(workspace_id, commitment_id)
        contact = self.repos.contacts.get(workspace_id, commitment.contact_id) if commitment.contact_id else None

        agent_run = AgentRun(id=new_id("run"), workspace_id=workspace_id, user_id=user_id, commitment_id=commitment_id, trigger=trigger)
        self.repos.agent_runs.save(agent_run)
        audit.record(
            self.repos, workspace_id, actor=user_id, event_type="agent_run.started", entity_type="commitment",
            entity_id=commitment_id, agent_run_id=agent_run.id, summary=f"Agent run started via '{trigger}'",
        )

        self.repos.commitments.update(workspace_id, commitment_id, lambda c: setattr(c, "status", CommitmentStatus.IN_PROGRESS))

        try:
            with self._step(agent_run, AgentStepName.RETRIEVAL, ["context_retriever.retrieve"]) as step:
                context = retrieval_step.retrieve_context(commitment, contact, self.repos)
                step.output_summary = f"{len(context['items'])} context item(s) ({context['status'].value})"

            with self._step(agent_run, AgentStepName.PLANNING, ["action_planner.select", "action_planner.plan"]) as step:
                planner = select_planner(commitment)
                plan = planner.plan(commitment, contact, context["items"], repos=self.repos, agent_run_id=agent_run.id)
                step.output_summary = f"Proposed action {plan['action'].id} ({plan['action'].type.value}) via {planner.name}"
        except Exception as exc:  # noqa: BLE001
            agent_run.status = AgentRunStatus.FAILED
            agent_run.error = str(exc)
            agent_run.ended_at = iso_now()
            self.repos.agent_runs.save(agent_run)
            self.repos.commitments.update(workspace_id, commitment_id, lambda c: setattr(c, "status", CommitmentStatus.FAILED))
            audit.record(
                self.repos, workspace_id, actor="agent", event_type="agent_run.failed", entity_type="commitment",
                entity_id=commitment_id, agent_run_id=agent_run.id, summary=str(exc),
            )
            raise

        action = plan["action"]
        with self._step(agent_run, AgentStepName.APPROVAL, ["approval.request_approval"]) as step:
            approval = approval_step.request_approval(action.id, workspace_id, self.repos)
            step.output_summary = f"Requested approval {approval.id} for action {action.id}"

        action = self.repos.actions.require(workspace_id, action.id)  # refresh: request_approval mutated it

        agent_run.status = AgentRunStatus.WAITING_FOR_APPROVAL
        agent_run.action_ids = [action.id]
        agent_run.approval_ids = [approval.id]
        agent_run.ended_at = iso_now()
        self.repos.agent_runs.save(agent_run)

        self.repos.commitments.update(workspace_id, commitment_id, lambda c: setattr(c, "status", CommitmentStatus.WAITING_FOR_APPROVAL))

        audit.record(
            self.repos, workspace_id, actor="agent", event_type="agent_run.waiting_for_approval", entity_type="action",
            entity_id=action.id, agent_run_id=agent_run.id, action_id=action.id, approval_id=approval.id,
            summary="Action proposed and is waiting for user approval",
        )

        return {
            "agent_run": agent_run,
            "commitment": self.repos.commitments.require(workspace_id, commitment_id),
            "contact": contact,
            "action_plan": plan.get("action_plan"),
            "document": plan["document"],
            "changes": plan["changes"],
            "draft": plan["draft"],
            "action": action,
            "approval": approval,
        }

    def execute_approved_action(self, workspace_id: str, action_id: str, *, actor: str) -> dict:
        action = self.repos.actions.require(workspace_id, action_id)
        agent_run = self.repos.agent_runs.get(workspace_id, action.agent_run_id) if action.agent_run_id else None

        if agent_run is None:
            raise ValueError(f"action '{action_id}' has no associated agent run")

        with self._step(agent_run, AgentStepName.EXECUTION, ["execution.execute_action"]) as step:
            executed = execution_step.execute_action(action_id, workspace_id, self.repos)
            step.output_summary = f"Action {action_id} -> {executed.status.value}"
            if executed.status.value == "failed":
                step.status = AgentStepStatus.FAILED
                step.error = executed.error

        commitment = None
        if executed.status.value == "executed" and action.commitment_id:
            with self._step(agent_run, AgentStepName.COMPLETION, ["completion.complete_commitment"]) as step:
                commitment = completion_step.complete_commitment(action.commitment_id, workspace_id, self.repos)
                step.output_summary = f"Commitment {action.commitment_id} completed"

        agent_run.status = AgentRunStatus.COMPLETED if executed.status.value == "executed" else AgentRunStatus.FAILED
        agent_run.ended_at = iso_now()
        if executed.status.value == "failed":
            agent_run.error = executed.error
        self.repos.agent_runs.save(agent_run)

        audit.record(
            self.repos, workspace_id, actor=actor, event_type=f"action.{executed.status.value}", entity_type="action",
            entity_id=action_id, agent_run_id=agent_run.id, action_id=action_id,
            summary=f"Action {action_id} {executed.status.value}",
        )

        return {"action": executed, "commitment": commitment, "agent_run": agent_run}
