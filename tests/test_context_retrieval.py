from __future__ import annotations

from datetime import datetime, timedelta, timezone

from promise_agent.context_retrieval import ContextItemType, ContextQuery, ContextRetriever, RetrievalStatus, TimeWindow
from promise_domain.models import Document, Message
from promise_shared.ids import new_id

"""Engine-level tests for the Context Retrieval Engine (`promise_agent.context_retrieval`).
Exercised directly against `ContextRetriever` — no commitment, REST, or MCP involved — so
these prove the retrieval/ranking/filtering/error-handling logic in isolation. See
`tests/test_commitment_context_application.py` for the commitment -> context integration,
REST, MCP, and agent-planning-level tests."""


def _query(ctx, **overrides) -> ContextQuery:
    base = dict(workspace_id=ctx.default_workspace_id, user_id=ctx.default_user_id, text="", keywords=[], limit=10)
    base.update(overrides)
    return ContextQuery(**base)


def _save_doc(ctx, *, name, content="", tags=None, created_at=None):
    doc = Document(
        id=new_id("doc"), workspace_id=ctx.default_workspace_id, name=name, type="text/plain",
        content_text=content, metadata={"tags": tags or []},
    )
    if created_at:
        doc.created_at = created_at
        doc.updated_at = created_at
    return ctx.repos.documents.save(doc)


def _save_msg(ctx, *, sender, recipient="Kurnia", subject="", content=""):
    msg = Message(id=new_id("msg"), workspace_id=ctx.default_workspace_id, sender=sender, recipient=recipient, subject=subject, content=content)
    return ctx.repos.messages.save(msg)


def _retriever(ctx) -> ContextRetriever:
    return ContextRetriever(ctx.integrations.get())


class _RogueIntegration:
    """A misbehaving provider that hands back another workspace's row — proves the
    retriever's defense-in-depth filter, independent of any one provider's own
    (correct) scoping."""

    provider_name = "rogue"

    def search_files(self, workspace_id: str, query: str) -> list[dict]:
        return [{"id": "doc_x", "workspace_id": "some-other-workspace", "name": "Leaked.txt", "content_text": "leaked", "metadata": {}}]

    def search_messages(self, workspace_id: str, query: str) -> list[dict]:
        return []

    def get_file(self, workspace_id, file_id):
        return None

    def get_message(self, workspace_id, message_id):
        return None

    def create_draft(self, *a, **k):
        raise NotImplementedError

    def send_message(self, *a, **k):
        raise NotImplementedError


class _PartlyBrokenIntegration:
    """Documents fail, messages succeed — proves partial retrieval + per-provider errors."""

    provider_name = "partly_broken"

    def search_files(self, workspace_id: str, query: str) -> list[dict]:
        raise RuntimeError("document search backend unavailable")

    def search_messages(self, workspace_id: str, query: str) -> list[dict]:
        return [{
            "id": "msg_ok", "workspace_id": workspace_id, "sender": "Andi", "recipient": "Kurnia",
            "subject": "s", "content": "revised proposal", "occurred_at": datetime.now(timezone.utc).isoformat(),
        }]

    def get_file(self, workspace_id, file_id):
        return None

    def get_message(self, workspace_id, message_id):
        return None

    def create_draft(self, *a, **k):
        raise NotImplementedError

    def send_message(self, *a, **k):
        raise NotImplementedError


class _FullyBrokenIntegration:
    provider_name = "fully_broken"

    def search_files(self, workspace_id: str, query: str) -> list[dict]:
        raise RuntimeError("boom")

    def search_messages(self, workspace_id: str, query: str) -> list[dict]:
        raise RuntimeError("boom")

    def get_file(self, workspace_id, file_id):
        return None

    def get_message(self, workspace_id, message_id):
        return None

    def create_draft(self, *a, **k):
        raise NotImplementedError

    def send_message(self, *a, **k):
        raise NotImplementedError


# ---- 1. contact match ranking -------------------------------------------------------------

def test_exact_contact_match_ranks_above_an_unrelated_item(ctx):
    _save_doc(ctx, name="Andi_Proposal.docx", content="revised proposal for andi", tags=["andi", "proposal"])
    _save_doc(ctx, name="Grocery_List.txt", content="milk eggs bread proposal")  # weak: has "proposal" but no contact

    result = _retriever(ctx).retrieve(_query(ctx, text="revised proposal", keywords=["revised", "proposal"], contact_name="Andi"))

    assert result.items[0].title == "Andi_Proposal.docx"
    assert any("Matched contact: Andi" in r for r in result.items[0].match_reasons)


# ---- 2. title/content keyword matching -----------------------------------------------------

def test_title_and_content_keyword_matches_outrank_no_match(ctx):
    _save_doc(ctx, name="Revised_Proposal.txt", content="the revised proposal is ready")
    _save_doc(ctx, name="Unrelated.txt", content="nothing relevant here at all")

    result = _retriever(ctx).retrieve(_query(ctx, text="revised proposal", keywords=["revised", "proposal"]))

    titles = [i.title for i in result.items]
    assert titles[0] == "Revised_Proposal.txt"
    assert "Unrelated.txt" not in titles  # never even matched a search term
    assert result.items[0].score > 0


# ---- 3. recency ranking ---------------------------------------------------------------------

def test_more_recent_item_ranks_above_an_older_one_with_equal_keyword_match(ctx):
    now = datetime.now(timezone.utc)
    _save_doc(ctx, name="Report_Old.txt", content="quarterly report", created_at=(now - timedelta(days=400)).isoformat())
    _save_doc(ctx, name="Report_New.txt", content="quarterly report", created_at=now.isoformat())

    result = _retriever(ctx).retrieve(_query(ctx, text="quarterly report", keywords=["quarterly", "report"]))

    assert result.items[0].title == "Report_New.txt"


# ---- 4. deduplication -----------------------------------------------------------------------

def test_same_artifact_matched_by_multiple_terms_is_returned_once_with_combined_reasons(ctx):
    _save_doc(ctx, name="Andi_Proposal.docx", content="revised proposal for andi", tags=["andi", "proposal"])

    result = _retriever(ctx).retrieve(_query(ctx, text="revised proposal", keywords=["revised", "proposal"], contact_name="Andi"))

    matches = [i for i in result.items if i.title == "Andi_Proposal.docx"]
    assert len(matches) == 1
    reasons = " ".join(matches[0].match_reasons).lower()
    assert "contact" in reasons
    assert "title" in reasons or "phrase" in reasons or "keyword" in reasons


# ---- 5. workspace isolation (defense in depth) -----------------------------------------------

def test_cross_workspace_candidate_is_dropped_even_if_a_provider_hands_one_back(ctx):
    result = ContextRetriever(_RogueIntegration()).retrieve(_query(ctx, text="leaked", keywords=["leaked"]))
    assert result.items == []


# ---- 7. type filtering -----------------------------------------------------------------------

def test_type_filter_excludes_the_other_type(ctx):
    _save_doc(ctx, name="Proposal.txt", content="proposal text")
    _save_msg(ctx, sender="Andi", subject="Proposal update", content="proposal text")

    doc_only = _retriever(ctx).retrieve(_query(ctx, text="proposal", keywords=["proposal"], types=[ContextItemType.DOCUMENT]))
    assert len(doc_only.items) == 1 and doc_only.items[0].type == ContextItemType.DOCUMENT

    msg_only = _retriever(ctx).retrieve(_query(ctx, text="proposal", keywords=["proposal"], types=[ContextItemType.MESSAGE]))
    assert len(msg_only.items) == 1 and msg_only.items[0].type == ContextItemType.MESSAGE


# ---- 8. date filtering -----------------------------------------------------------------------

def test_time_window_filters_out_items_outside_the_range(ctx):
    now = datetime.now(timezone.utc)
    _save_doc(ctx, name="Recent_Report.txt", content="report", created_at=(now - timedelta(days=1)).isoformat())
    _save_doc(ctx, name="Old_Report.txt", content="report", created_at=(now - timedelta(days=100)).isoformat())

    window = TimeWindow(start=(now - timedelta(days=5)).isoformat())
    result = _retriever(ctx).retrieve(_query(ctx, text="report", keywords=["report"], time_window=window))

    titles = [i.title for i in result.items]
    assert "Recent_Report.txt" in titles
    assert "Old_Report.txt" not in titles


# ---- 9. result limit -------------------------------------------------------------------------

def test_result_limit_is_respected(ctx):
    for n in range(8):
        _save_doc(ctx, name=f"Report_{n}.txt", content="report content")

    result = _retriever(ctx).retrieve(_query(ctx, text="report", keywords=["report"], limit=3))
    assert len(result.items) == 3


# ---- 10. match explanations -------------------------------------------------------------------

def test_every_result_carries_at_least_one_human_readable_match_reason(ctx):
    _save_doc(ctx, name="Andi_Proposal.docx", content="revised proposal", tags=["andi"])
    result = _retriever(ctx).retrieve(_query(ctx, text="revised proposal", keywords=["revised", "proposal"], contact_name="Andi"))
    assert result.items
    for item in result.items:
        assert item.match_reasons
        assert all(isinstance(r, str) and r for r in item.match_reasons)


# ---- 11. provider failure handling / 12. partial retrieval ------------------------------------

def test_one_provider_failing_does_not_fabricate_results_and_marks_retrieval_partial(ctx):
    result = ContextRetriever(_PartlyBrokenIntegration()).retrieve(
        _query(ctx, text="revised proposal", keywords=["revised", "proposal"], contact_name="Andi")
    )

    assert result.status == RetrievalStatus.PARTIAL
    assert len(result.errors) == 1
    assert result.errors[0]["provider"] == "documents"
    assert any(i.type == ContextItemType.MESSAGE for i in result.items)  # the surviving provider's results are kept


def test_all_providers_failing_is_a_controlled_partial_result_not_a_crash(ctx):
    result = ContextRetriever(_FullyBrokenIntegration()).retrieve(_query(ctx, text="anything", keywords=["anything"]))
    assert result.status == RetrievalStatus.PARTIAL
    assert result.items == []
    assert len(result.errors) == 2


# ---- 15. irrelevant artifacts excluded/ranked lower --------------------------------------------

def test_weakly_relevant_artifact_ranks_below_a_strongly_relevant_one(ctx):
    _save_doc(ctx, name="Andi_Proposal.docx", content="revised proposal for andi", tags=["andi", "proposal"])
    _save_doc(ctx, name="Old_Proposal_Template.txt", content="a generic proposal template with no specifics")

    result = _retriever(ctx).retrieve(_query(ctx, text="revised proposal", keywords=["revised", "proposal"], contact_name="Andi"))
    titles = [i.title for i in result.items]
    assert titles.index("Andi_Proposal.docx") < titles.index("Old_Proposal_Template.txt")


def test_irrelevant_artifact_never_becomes_a_candidate_at_all(ctx):
    _save_doc(ctx, name="Andi_Proposal.docx", content="revised proposal for andi", tags=["andi", "proposal"])
    _save_doc(ctx, name="Grocery_List.txt", content="milk eggs bread")

    result = _retriever(ctx).retrieve(_query(ctx, text="revised proposal", keywords=["revised", "proposal"], contact_name="Andi"))
    assert "Grocery_List.txt" not in [i.title for i in result.items]


# ---- request id / determinism sanity ------------------------------------------------------------

def test_scores_are_normalized_between_zero_and_one(ctx):
    _save_doc(ctx, name="Andi_Proposal.docx", content="revised proposal for andi", tags=["andi", "proposal"])
    result = _retriever(ctx).retrieve(_query(ctx, text="revised proposal", keywords=["revised", "proposal"], contact_name="Andi"))
    for item in result.items:
        assert 0.0 <= item.score <= 1.0
