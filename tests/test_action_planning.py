from __future__ import annotations

import pytest
from promise_agent.action_planning import ActionPlan, PlanningError, SendMessagePlanner, select_planner
from promise_app import tools
from promise_domain.enums import ActionStatus, ActionType, CommitmentStatus
from promise_domain.models import Contact, Document
from promise_shared.errors import ApprovalRequiredError, VerifiedContactRequiredError
from promise_shared.ids import new_id

"""Tests for the Action Planning Engine (`promise_agent.action_planning`). See
`tests/test_commitment_context_application.py` for the retrieval -> planning
integration test and `tests/test_llm_provider_errors.py` for the Bedrod
document-revision error-handling tests."""


def _create(ctx, text, **kwargs):
    return tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text=text, **kwargs)


# ---- 1. planner interface --------------------------------------------------------------------

def test_send_message_planner_satisfies_the_action_planner_interface():
    planner = SendMessagePlanner()
    assert hasattr(planner, "name") and isinstance(planner.name, str)
    assert callable(planner.supports)
    assert callable(planner.plan)


# ---- 2. planner selection ---------------------------------------------------------------------

def test_select_planner_picks_send_message_planner_for_a_send_type_commitment(seeded_ctx):
    commitment = _create(seeded_ctx, "I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    planner = select_planner(commitment)
    assert isinstance(planner, SendMessagePlanner)


@pytest.mark.parametrize("verb_text", ["I'll send Andi the update.", "I'll email Andi the report.", "I'll share the doc with Andi."])
def test_select_planner_recognizes_every_send_type_verb(seeded_ctx, verb_text):
    commitment = _create(seeded_ctx, verb_text)["commitment"]
    assert isinstance(select_planner(commitment), SendMessagePlanner)


# ---- 4. unsupported commitment fails safely -----------------------------------------------------

def test_select_planner_raises_planning_error_for_an_unsupported_commitment_type(ctx):
    commitment = _create(ctx, "I need to call Sarah on Friday.")["commitment"]
    with pytest.raises(PlanningError, match="does not yet know how to execute"):
        select_planner(commitment)


def test_unsupported_commitment_fails_the_agent_run_safely_not_silently(ctx):
    commitment = _create(ctx, "I need to call Sarah on Friday.")["commitment"]

    with pytest.raises(PlanningError):
        tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    failed = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, commitment_id=commitment.id)
    assert failed.status.value == "failed"
    # no Action was ever fabricated for this commitment
    assert tools.list_actions(ctx, workspace_id=ctx.default_workspace_id, commitment_id=commitment.id) == []


# ---- 3 / 5 / 7. supported workflow, ContextItem[] consumption, proposed Action -------------------

def test_supported_proposal_workflow_produces_a_proposed_action(seeded_ctx):
    ctx = seeded_ctx
    commitment = _create(ctx, "I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id)

    from promise_agent.steps import retrieval as retrieval_step

    context = retrieval_step.retrieve_context(commitment, contact, ctx.agent_repos)
    assert context["items"] and all(hasattr(i, "type") and hasattr(i, "score") for i in context["items"])

    plan = SendMessagePlanner().plan(commitment, contact, context["items"], repos=ctx.agent_repos, agent_run_id=new_id("run"))

    assert isinstance(plan["action_plan"], ActionPlan)
    assert plan["action_plan"].action_type == ActionType.SEND_MESSAGE
    assert plan["action_plan"].target_contact_id == contact.id
    assert plan["action_plan"].supporting_context_ids  # explainable: which ContextItems it used

    action = plan["action"]
    assert action.status == ActionStatus.PROPOSED
    assert action.type == ActionType.SEND_MESSAGE
    assert action.commitment_id == commitment.id


# ---- 6. planner does not execute side effects or request approval ------------------------------

def test_planner_does_not_send_or_request_approval_or_complete_the_commitment(seeded_ctx):
    ctx = seeded_ctx
    commitment = _create(ctx, "I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id)

    from promise_agent.steps import retrieval as retrieval_step

    context = retrieval_step.retrieve_context(commitment, contact, ctx.agent_repos)
    SendMessagePlanner().plan(commitment, contact, context["items"], repos=ctx.agent_repos, agent_run_id=new_id("run"))

    # No Approval row exists yet -- only AgentOrchestrator's approval step creates one.
    assert ctx.repos.approvals.list(ctx.default_workspace_id) == []
    # The commitment is untouched by planning -- still whatever create_commitment left it as.
    refreshed = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, commitment_id=commitment.id)
    assert refreshed.status != CommitmentStatus.COMPLETED
    # No draft was actually sent (LocalIntegrationProvider tracks real sends).
    drafts = [d for d in ctx.store.query("draft", ctx.default_workspace_id)]
    assert all(d.get("status") != "sent" for d in drafts)


# ---- 8. verified contact requirement -------------------------------------------------------------

def test_planner_requires_a_verified_contact_email(seeded_ctx):
    ctx = seeded_ctx
    commitment = _create(ctx, "I'll send Priya the update today.")["commitment"]
    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id)
    assert contact is not None and contact.email is None  # not seeded, no invented email

    from promise_agent.steps import retrieval as retrieval_step

    context = retrieval_step.retrieve_context(commitment, contact, ctx.agent_repos)
    with pytest.raises(VerifiedContactRequiredError):
        SendMessagePlanner().plan(commitment, contact, context["items"], repos=ctx.agent_repos, agent_run_id=new_id("run"))


# ---- 9. approval is still mandatory --------------------------------------------------------------

def test_approval_remains_mandatory_after_the_planning_refactor(seeded_ctx):
    ctx = seeded_ctx
    commitment = _create(ctx, "I'll send Andi the revised proposal tomorrow morning.")["commitment"]

    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert handled["action"].status.value == "waiting_for_approval"
    assert handled["approval"].status.value == "pending"

    with pytest.raises(ApprovalRequiredError):
        tools.execute_approved_action(ctx, workspace_id=ctx.default_workspace_id, action_id=handled["action"].id, actor=ctx.default_user_id)


# ---- 10. full flow --------------------------------------------------------------------------------

def test_full_flow_retrieval_planning_approval_execution_completion(seeded_ctx):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    commitment = _create(ctx, "I'll send Andi the revised proposal tomorrow morning.")["commitment"]

    handled = tools.handle_commitment(ctx, workspace_id=ws, user_id=uid, commitment_id=commitment.id)
    assert handled["action_plan"] is not None
    assert handled["action"].status.value == "waiting_for_approval"

    tools.decide_approval(ctx, workspace_id=ws, approval_id=handled["approval"].id, decision="approved", decided_by=uid)
    result = tools.execute_approved_action(ctx, workspace_id=ws, action_id=handled["action"].id, actor=uid)

    assert result["action"].status.value == "executed"
    assert result["commitment"].status.value == "completed"


# ---- message composition: no hard-coded subject/body -----------------------------------------------

def test_message_subject_is_derived_from_the_commitment_not_hard_coded(seeded_ctx):
    ctx = seeded_ctx
    commitment = _create(ctx, "I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert handled["draft"]["subject"] == commitment.title
    assert handled["draft"]["subject"] != "Revised proposal"  # the old hard-coded literal


# ---- regression: no Andi/Sarah/filename hard-coded into planner selection -------------------------

def test_planner_selection_is_not_hard_coded_to_any_specific_name_or_file(ctx):
    """Uses a workspace with NONE of the seeded Andi/Sarah demo data -- a different
    contact, a different document, a different workspace -- to prove the planner
    selection and planning logic is generic, not pinned to demo names."""
    zephyr = ctx.repos.contacts.save(
        Contact(id=new_id("con"), workspace_id=ctx.default_workspace_id, name="Zephyr", email="zephyr@example.com")
    )
    ctx.repos.documents.save(
        Document(
            id=new_id("doc"), workspace_id=ctx.default_workspace_id, name="Quarterly_Budget_Draft.pdf",
            type="application/pdf", content_text="Q3 budget line items and totals", metadata={"tags": ["budget", "zephyr"]},
        )
    )

    commitment = _create(ctx, "I'll send Zephyr the quarterly budget draft tomorrow.")["commitment"]
    assert commitment.contact_id == zephyr.id

    planner = select_planner(commitment)
    assert isinstance(planner, SendMessagePlanner)

    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert handled["action"].status.value == "waiting_for_approval"
    assert "Budget_Draft" in handled["document"].name
    assert handled["draft"]["recipient"] == "zephyr@example.com"
