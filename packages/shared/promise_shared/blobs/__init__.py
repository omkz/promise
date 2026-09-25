from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from .local_blob_store import LocalBlobStore
from .s3_blob_store import S3BlobStore


@dataclass(frozen=True)
class BlobObject:
    """One binary artifact read back from a `DocumentBlobStore` -- the exact
    bytes originally written, plus the metadata needed to re-attach/re-serve
    them correctly (content type, filename, size)."""

    data: bytes
    content_type: str
    filename: str
    size_bytes: int


class DocumentBlobStore(Protocol):
    """Where PROMISE keeps the actual binary bytes of a generated/uploaded
    document artifact -- distinct from `Document.content_text` (extracted
    text, for retrieval/AI/search) and distinct from `EntityStore` (workspace
    metadata only; DynamoDB/local JSON never holds a binary blob). A
    `Document`'s `storage_key` is a pointer (`ref`) into this store, the same
    "pointer, not the payload, lives in the metadata record" shape
    `IntegrationAccount.secret_ref` already uses for `SecretStore`.

    `ref` is an opaque, backend-agnostic string derived centrally by
    `blob_ref()` below (never a caller-supplied path/key) -- see that
    function's docstring for why.
    """

    def put(self, ref: str, data: bytes, *, content_type: str, filename: str) -> None: ...

    def get(self, ref: str) -> BlobObject | None: ...

    def delete(self, ref: str) -> None: ...


def blob_ref(workspace_id: str, document_id: str) -> str:
    """The one place a `Document`'s blob-store reference is derived from its
    identity -- every caller (the revision planner writing an artifact, the
    Gmail provider reading one back, a future download route) goes through
    this function rather than building a path/key by hand, so a workspace_id
    is always baked into the reference and no caller can ever construct one
    pointing at another workspace's artifact."""
    return f"document:{workspace_id}:{document_id}"


def build_blob_store() -> DocumentBlobStore:
    """`BLOB_STORE_BACKEND=s3` (recommended whenever `STORAGE_BACKEND=dynamodb`,
    i.e. any real deployment) -> S3. Unset/anything else -> the local
    filesystem store -- local development and tests only, the same
    "never real production storage, isolated behind one interface" split
    `SECRET_STORE_BACKEND`/`AUTH_MODE=local` already use."""
    backend = os.getenv("BLOB_STORE_BACKEND", "local").lower()
    if backend == "s3":
        return S3BlobStore(
            bucket=os.environ["S3_BUCKET"],
            region=os.getenv("AWS_REGION", "us-east-1"),
            key_prefix=os.getenv("S3_DOCUMENTS_PREFIX", "documents/"),
        )
    return LocalBlobStore(os.getenv("LOCAL_DOCUMENTS_DIR", "./data/documents"))


__all__ = ["BlobObject", "DocumentBlobStore", "blob_ref", "build_blob_store", "LocalBlobStore", "S3BlobStore"]
