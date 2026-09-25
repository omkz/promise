from __future__ import annotations

import pytest
from promise_app import tools
from promise_domain.models import Document, Workspace
from promise_shared.blobs import blob_ref
from promise_shared.errors import DocumentArtifactMissing, NotFoundError
from promise_shared.ids import new_id

"""tools.get_document_artifact -- the application-level "download the binary
artifact" seam (distinct from get_file, which returns content_text). Ownership
here is workspace-scoped, matching Document's existing (non-per-user)
ownership model -- see that function's own docstring."""


def _make_document(ctx, *, workspace_id, with_artifact=True, artifact_bytes=b"binary artifact bytes"):
    document_id = new_id("doc")
    storage_key = None
    artifact_size = None
    if with_artifact:
        storage_key = blob_ref(workspace_id, document_id)
        ctx.blob_store.put(storage_key, artifact_bytes, content_type="application/octet-stream", filename="artifact.bin")
        artifact_size = len(artifact_bytes)
    document = Document(
        id=document_id, workspace_id=workspace_id, name="artifact.bin", type="application/octet-stream",
        content_text="extracted text, never the attachment itself", storage_key=storage_key, artifact_size_bytes=artifact_size,
    )
    ctx.repos.documents.save(document)
    return document


def test_returns_exact_artifact_bytes_and_metadata(ctx):
    document = _make_document(ctx, workspace_id=ctx.default_workspace_id, artifact_bytes=b"exact bytes here")
    result = tools.get_document_artifact(ctx, workspace_id=ctx.default_workspace_id, document_id=document.id)
    assert result["data"] == b"exact bytes here"
    assert result["filename"] == "artifact.bin"
    assert result["content_type"] == "application/octet-stream"
    assert result["size_bytes"] == len(b"exact bytes here")


def test_never_returns_content_text_as_the_artifact(ctx):
    document = _make_document(ctx, workspace_id=ctx.default_workspace_id, artifact_bytes=b"real binary artifact")
    result = tools.get_document_artifact(ctx, workspace_id=ctx.default_workspace_id, document_id=document.id)
    assert result["data"] != document.content_text.encode("utf-8")


def test_raises_document_artifact_missing_when_no_storage_key(ctx):
    document = _make_document(ctx, workspace_id=ctx.default_workspace_id, with_artifact=False)
    with pytest.raises(DocumentArtifactMissing):
        tools.get_document_artifact(ctx, workspace_id=ctx.default_workspace_id, document_id=document.id)


def test_raises_document_artifact_missing_when_blob_deleted_out_from_under_it(ctx):
    document = _make_document(ctx, workspace_id=ctx.default_workspace_id)
    ctx.blob_store.delete(document.storage_key)
    with pytest.raises(DocumentArtifactMissing):
        tools.get_document_artifact(ctx, workspace_id=ctx.default_workspace_id, document_id=document.id)


def test_raises_not_found_for_a_nonexistent_document(ctx):
    with pytest.raises(NotFoundError):
        tools.get_document_artifact(ctx, workspace_id=ctx.default_workspace_id, document_id="doc_never_existed")


def test_document_artifact_does_not_leak_across_workspaces(ctx):
    other_ws = Workspace(id=new_id("ws"), name="Other Co", slug="other-co")
    ctx.repos.workspaces.save(other_ws)
    document = _make_document(ctx, workspace_id=ctx.default_workspace_id)

    with pytest.raises(NotFoundError):
        tools.get_document_artifact(ctx, workspace_id=other_ws.id, document_id=document.id)


# ---- REST -----------------------------------------------------------------------------------------

def test_rest_document_artifact_download(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    ws = seeded_ctx.default_workspace_id
    document = _make_document(seeded_ctx, workspace_id=ws, artifact_bytes=b"downloadable bytes")

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.get(f"/api/documents/{document.id}/artifact")
        assert r.status_code == 200
        assert r.content == b"downloadable bytes"
        assert r.headers["content-type"] == "application/octet-stream"
        assert "artifact.bin" in r.headers["content-disposition"]
    finally:
        app.dependency_overrides.clear()


def test_rest_document_artifact_missing_is_409(seeded_ctx):
    from fastapi.testclient import TestClient
    from promise_api import deps
    from promise_api.main import app

    ws = seeded_ctx.default_workspace_id
    document = _make_document(seeded_ctx, workspace_id=ws, with_artifact=False)

    app.dependency_overrides[deps.get_context] = lambda: seeded_ctx
    try:
        client = TestClient(app)
        r = client.get(f"/api/documents/{document.id}/artifact")
        assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()
