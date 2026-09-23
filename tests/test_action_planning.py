from __future__ import annotations

import pytest
from promise_agent.action_planning import (
    ActionPlan,
    PlanningError,
    SendExistingDocumentPlanner,
    SendMessagePlanner,
    SendRevisedDocumentPlanner,
    select_planner,
)
from promise_app import tools
from promise_domain.enums import ActionStatus, ActionType, CommitmentStatus
from promise_domain.models import Contact, Document
from promise_shared.errors import ApprovalRequiredError, VerifiedContactRequiredError
from promise_shared.ids import new_id

"""Tests for the Action Planning Engine (`promise_agent.action_planning`). See
`tests/test_commitment_context_application.py` for the retrieval -> planning
integration test and `tests/test_llm_provider_errors.py` for the Bedrock
document-revision error-handling tests.

Two concrete planners: `SendRevisedDocumentPlanner` (alias: `SendMessagePlanner`,
kept for backward compatibility) handles "send the revised/updated X" and is the
ONLY one that ever calls `llm.revise_document`; `SendExistingDocumentPlanner`
handles a plain "send X" with no revision language and never touches the LLM.
"""


def _create(ctx, text, **kwargs):
    return tools.create_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text=text, **kwargs)


# ---- 1. planner interface --------------------------------------------------------------------

@pytest.mark.parametrize("planner_cls", [SendRevisedDocumentPlanner, SendExistingDocumentPlanner])
def test_planner_satisfies_the_action_planner_interface(planner_cls):
    planner = planner_cls()
    assert hasattr(planner, "name") and isinstance(planner.name, str)
    assert callable(planner.supports)
    assert callable(planner.plan)


def test_send_message_planner_is_the_revised_document_planner():
    """Backward-compatible alias: `SendMessagePlanner` is `SendRevisedDocumentPlanner`,
    not a separate/different class."""
    assert SendMessagePlanner is SendRevisedDocumentPlanner


# ---- 2. planner selection: revision language -> SendRevisedDocumentPlanner ---------------------

@pytest.mark.parametrize("revision_text", [
    "I'll send Andi the revised proposal tomorrow morning.",
    "I'll send Andi the update.",
    "I'll email Andi the updated report.",
    "I'll share the revised doc with Andi.",
])
def test_select_planner_picks_the_revision_planner_when_revision_language_is_present(seeded_ctx, revision_text):
    commitment = _create(seeded_ctx, revision_text)["commitment"]
    assert isinstance(select_planner(commitment), SendRevisedDocumentPlanner)


# ---- 2. planner selection: plain send/email/share -> SendExistingDocumentPlanner, NOT revision --

@pytest.mark.parametrize("plain_text", [
    "I'll email Andi the report.",
    "I'll share the doc with Andi.",
    "I'll deliver the proposal to Andi.",
])
def test_select_planner_picks_the_existing_document_planner_when_no_revision_language(seeded_ctx, plain_text):
    """The core regression: a generic send/email/share verb alone must NOT route
    to the revision planner — only explicit revision language does."""
    commitment = _create(seeded_ctx, plain_text)["commitment"]
    planner = select_planner(commitment)
    assert isinstance(planner, SendExistingDocumentPlanner)
    assert not isinstance(planner, SendRevisedDocumentPlanner)


# ---- 4. unsupported commitment fails safely -----------------------------------------------------

def test_select_planner_raises_planning_error_for_an_unsupported_commitment_type(ctx):
    commitment = _create(ctx, "I need to call Sarah on Friday.")["commitment"]
    with pytest.raises(PlanningError, match="does not yet know how to execute"):
        select_planner(commitment)


def test_unsupported_commitment_fails_the_agent_run_safely_not_silently(ctx):
    commitment = _create(ctx, "I need to call Sarah on Friday.")["commitment"]

    with pytest.raises(PlanningError):
        tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)

    failed = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert failed.status.value == "failed"
    # no Action was ever fabricated for this commitment
    assert tools.list_actions(ctx, workspace_id=ctx.default_workspace_id, commitment_id=commitment.id) == []


# ---- 3 / 5 / 7. supported revision workflow, ContextItem[] consumption, proposed Action ----------

def test_supported_revision_workflow_produces_a_proposed_action(seeded_ctx):
    ctx = seeded_ctx
    commitment = _create(ctx, "I'll send Andi the revised proposal tomorrow morning.")["commitment"]
    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id)

    from promise_agent.steps import retrieval as retrieval_step

    context = retrieval_step.retrieve_context(commitment, contact, ctx.agent_repos)
    assert context["items"] and all(hasattr(i, "type") and hasattr(i, "score") for i in context["items"])

    plan = SendRevisedDocumentPlanner().plan(commitment, contact, context["items"], repos=ctx.agent_repos, agent_run_id=new_id("run"))

    assert isinstance(plan["action_plan"], ActionPlan)
    assert plan["action_plan"].action_type == ActionType.SEND_MESSAGE
    assert plan["action_plan"].target_contact_id == contact.id
    assert plan["action_plan"].supporting_context_ids  # explainable: which ContextItems it used
    assert plan["changes"]  # a real revision happened

    action = plan["action"]
    assert action.status == ActionStatus.PROPOSED
    assert action.type == ActionType.SEND_MESSAGE
    assert action.commitment_id == commitment.id


# ---- send-existing workflow: no revision, never touches the LLM ---------------------------------

def test_supported_existing_document_workflow_produces_a_proposed_action_without_revising(seeded_ctx, monkeypatch):
    ctx = seeded_ctx
    commitment = _create(ctx, "I'll email Andi the proposal.")["commitment"]
    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id)
    assert select_planner(commitment) is not None
    planner = select_planner(commitment)
    assert isinstance(planner, SendExistingDocumentPlanner)

    from promise_agent import llm
    from promise_agent.steps import retrieval as retrieval_step

    def _fail_if_called(*a, **k):
        raise AssertionError("SendExistingDocumentPlanner must never call llm.revise_document")

    monkeypatch.setattr(llm, "revise_document", _fail_if_called)

    context = retrieval_step.retrieve_context(commitment, contact, ctx.agent_repos)
    plan = planner.plan(commitment, contact, context["items"], repos=ctx.agent_repos, agent_run_id=new_id("run"))

    assert plan["changes"] == []  # nothing was revised
    assert plan["document"].name == "Andi_Proposal_v3.docx"  # the EXISTING document, unchanged name
    assert "_revised" not in plan["document"].name

    action = plan["action"]
    assert action.status == ActionStatus.PROPOSED
    assert action.type == ActionType.SEND_MESSAGE

    # No new Document row was created for this plan.
    all_docs = ctx.repos.documents.list(ctx.default_workspace_id)
    assert plan["document"].id in [d.id for d in all_docs]
    assert not any(d.metadata.get("agent_generated") for d in all_docs)


def test_existing_document_message_never_claims_a_revision_happened(seeded_ctx):
    ctx = seeded_ctx
    commitment = _create(ctx, "I'll email Andi the proposal.")["commitment"]
    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    body = handled["draft"]["body"].lower()
    assert "summary of changes" not in body
    assert "revised" not in body


# ---- 6. planner does not execute side effects or request approval (both planners) ---------------

@pytest.mark.parametrize("text", ["I'll send Andi the revised proposal tomorrow morning.", "I'll email Andi the proposal."])
def test_planner_does_not_send_or_request_approval_or_complete_the_commitment(seeded_ctx, text):
    ctx = seeded_ctx
    commitment = _create(ctx, text)["commitment"]
    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id)

    from promise_agent.steps import retrieval as retrieval_step

    context = retrieval_step.retrieve_context(commitment, contact, ctx.agent_repos)
    select_planner(commitment).plan(commitment, contact, context["items"], repos=ctx.agent_repos, agent_run_id=new_id("run"))

    # No Approval row exists yet -- only AgentOrchestrator's approval step creates one.
    assert ctx.repos.approvals.list(ctx.default_workspace_id) == []
    # The commitment is untouched by planning -- still whatever create_commitment left it as.
    refreshed = tools.get_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert refreshed.status != CommitmentStatus.COMPLETED
    # No draft was actually sent (LocalIntegrationProvider tracks real sends).
    drafts = ctx.store.query("draft", ctx.default_workspace_id)
    assert all(d.get("status") != "sent" for d in drafts)


# ---- 8. verified contact requirement (both planners) ---------------------------------------------

@pytest.mark.parametrize("text,planner_cls", [
    ("I'll send Priya the update today.", SendRevisedDocumentPlanner),
    ("I'll email Priya the proposal.", SendExistingDocumentPlanner),
])
def test_planner_requires_a_verified_contact_email(seeded_ctx, text, planner_cls):
    ctx = seeded_ctx
    commitment = _create(ctx, text)["commitment"]
    contact = ctx.repos.contacts.get(ctx.default_workspace_id, commitment.contact_id)
    assert contact is not None and contact.email is None  # not seeded, no invented email
    assert isinstance(select_planner(commitment), planner_cls)

    from promise_agent.steps import retrieval as retrieval_step

    context = retrieval_step.retrieve_context(commitment, contact, ctx.agent_repos)
    with pytest.raises(VerifiedContactRequiredError):
        planner_cls().plan(commitment, contact, context["items"], repos=ctx.agent_repos, agent_run_id=new_id("run"))


# ---- 9. approval is still mandatory --------------------------------------------------------------

@pytest.mark.parametrize("text", ["I'll send Andi the revised proposal tomorrow morning.", "I'll email Andi the proposal."])
def test_approval_remains_mandatory_after_the_planning_refactor(seeded_ctx, text):
    ctx = seeded_ctx
    commitment = _create(ctx, text)["commitment"]

    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert handled["action"].status.value == "waiting_for_approval"
    assert handled["approval"].status.value == "pending"

    with pytest.raises(ApprovalRequiredError):
        tools.execute_approved_action(ctx, workspace_id=ctx.default_workspace_id, action_id=handled["action"].id, actor=ctx.default_user_id)


# ---- 10. full flow ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["I'll send Andi the revised proposal tomorrow morning.", "I'll email Andi the proposal."])
def test_full_flow_retrieval_planning_approval_execution_completion(seeded_ctx, text):
    ctx = seeded_ctx
    ws, uid = ctx.default_workspace_id, ctx.default_user_id
    commitment = _create(ctx, text)["commitment"]

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

def test_revision_planner_selection_is_not_hard_coded_to_any_specific_name_or_file(ctx):
    """Uses a workspace with NONE of the seeded Andi/Sarah demo data -- a different
    contact, a different document, a different workspace -- to prove the revision
    planner's selection and planning logic is generic, not pinned to demo names."""
    zephyr = ctx.repos.contacts.save(
        Contact(id=new_id("con"), workspace_id=ctx.default_workspace_id, name="Zephyr", email="zephyr@example.com")
    )
    ctx.repos.documents.save(
        Document(
            id=new_id("doc"), workspace_id=ctx.default_workspace_id, name="Quarterly_Budget_Draft.pdf",
            type="application/pdf", content_text="Q3 budget line items and totals", metadata={"tags": ["budget", "zephyr"]},
        )
    )

    commitment = _create(ctx, "I'll send Zephyr the revised quarterly budget draft tomorrow.")["commitment"]
    assert commitment.contact_id == zephyr.id

    planner = select_planner(commitment)
    assert isinstance(planner, SendRevisedDocumentPlanner)

    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert handled["action"].status.value == "waiting_for_approval"
    assert "Budget_Draft" in handled["document"].name
    assert handled["draft"]["recipient"] == "zephyr@example.com"


def test_existing_document_planner_selection_is_not_hard_coded_to_any_specific_name_or_file(ctx):
    """Same proof as above, for `SendExistingDocumentPlanner` -- a plain send with
    no revision language, on entirely generic demo-free data."""
    orion = ctx.repos.contacts.save(
        Contact(id=new_id("con"), workspace_id=ctx.default_workspace_id, name="Orion", email="orion@example.com")
    )
    ctx.repos.documents.save(
        Document(
            id=new_id("doc"), workspace_id=ctx.default_workspace_id, name="Vendor_Contract.pdf",
            type="application/pdf", content_text="Standard vendor contract terms", metadata={"tags": ["contract", "orion"]},
        )
    )

    commitment = _create(ctx, "I'll email Orion the vendor contract.")["commitment"]
    assert commitment.contact_id == orion.id

    planner = select_planner(commitment)
    assert isinstance(planner, SendExistingDocumentPlanner)

    handled = tools.handle_commitment(ctx, workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, commitment_id=commitment.id)
    assert handled["action"].status.value == "waiting_for_approval"
    assert handled["document"].name == "Vendor_Contract.pdf"  # unchanged -- not revised
    assert handled["draft"]["recipient"] == "orion@example.com"
    assert handled["changes"] == []
