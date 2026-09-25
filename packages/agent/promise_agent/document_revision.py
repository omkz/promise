from __future__ import annotations

import re
from typing import Any

from promise_domain.models import Document
from promise_domain.repository import Repository
from promise_shared.blobs import DocumentBlobStore, blob_ref
from promise_shared.ids import new_id

from .document_artifacts import generate_revised_artifact

"""Shared "persist a revised document" helper -- the one place that builds a
real binary artifact for revised text, stores it, and saves the `Document`
row, so `SendRevisedDocumentPlanner` (the agent's own autonomous revision
during `handle_commitment`) and `promise_app.tools.prepare_revision` (the
manual, on-demand revision endpoint) can never drift apart on what a
"revised document" actually contains. Both call sites do the exact same
`revise_document -> build_revised_document` sequence; only the LLM call
itself (and its `feedback` source) differs between them.
"""


def build_revised_document(
    *, workspace_id: str, source_doc: dict[str, Any], revised_text: str, changes: list[str],
    documents: Repository[Document], blob_store: DocumentBlobStore,
) -> Document:
    """`revised_text` is the extracted-text concern (persisted as `content_text`,
    for retrieval/AI/search). The binary artifact is a separate concern: a real
    artifact matching `source_doc`'s own format (DOCX via python-docx, or the
    text's own UTF-8 bytes under its own real content type otherwise -- see
    `generate_revised_artifact`), written to `blob_store` *before* the
    `Document` row is saved, so `storage_key`/`artifact_size_bytes` are correct
    from the first write, never a separate update after the fact.
    """
    document_id = new_id("doc")
    artifact_bytes, artifact_content_type = generate_revised_artifact(
        source_type=source_doc.get("type", "text/plain"), revised_text=revised_text
    )
    storage_key = blob_ref(workspace_id, document_id)
    revised_name = re.sub(r"(\.[^.]+)$", r"_revised\1", source_doc["name"])
    blob_store.put(storage_key, artifact_bytes, content_type=artifact_content_type, filename=revised_name)

    document = Document(
        id=document_id,
        workspace_id=workspace_id,
        name=revised_name,
        type=artifact_content_type,
        content_text=revised_text,
        storage_key=storage_key,
        artifact_size_bytes=len(artifact_bytes),
        metadata={"derived_from": source_doc["id"], "changes": changes, "agent_generated": True},
    )
    documents.save(document)
    return document
