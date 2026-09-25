from __future__ import annotations

from promise_shared.blobs import blob_ref
from promise_shared.blobs.local_blob_store import LocalBlobStore
from promise_shared.blobs.s3_blob_store import S3BlobStore

"""DocumentBlobStore coverage: LocalBlobStore against the real filesystem
(tmp_path-isolated) and S3BlobStore against a hand-written fake boto3 client
(same style as tests/test_dynamodb_index.py/test_gmail_provider.py) -- no live
AWS credentials or network required."""


# ---- blob_ref: the central, workspace-baked key derivation -----------------------------------------

def test_blob_ref_bakes_workspace_id_into_the_reference():
    ref_a = blob_ref("ws_a", "doc_1")
    ref_b = blob_ref("ws_b", "doc_1")
    assert ref_a != ref_b  # same document id, different workspace -> different, non-colliding refs


def test_blob_ref_is_deterministic():
    assert blob_ref("ws_1", "doc_1") == blob_ref("ws_1", "doc_1")


# ---- LocalBlobStore ----------------------------------------------------------------------------

def test_local_store_put_then_get_returns_exact_bytes(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    data = b"\x00\x01\xffbinary content, not text"
    store.put("ref_1", data, content_type="application/octet-stream", filename="artifact.bin")

    blob = store.get("ref_1")
    assert blob is not None
    assert blob.data == data


def test_local_store_preserves_metadata(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    store.put("ref_1", b"hello", content_type="text/plain", filename="hello.txt")

    blob = store.get("ref_1")
    assert blob.content_type == "text/plain"
    assert blob.filename == "hello.txt"
    assert blob.size_bytes == 5


def test_local_store_missing_ref_returns_none_not_an_exception(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    assert store.get("never-written") is None


def test_local_store_put_overwrites_on_the_same_ref(tmp_path):
    """Matches EntityStore.put's own upsert semantics -- the last write wins,
    no separate versioning layer here."""
    store = LocalBlobStore(str(tmp_path))
    store.put("ref_1", b"version one", content_type="text/plain", filename="a.txt")
    store.put("ref_1", b"version two", content_type="text/plain", filename="b.txt")

    blob = store.get("ref_1")
    assert blob.data == b"version two"
    assert blob.filename == "b.txt"


def test_local_store_delete_removes_the_artifact(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    store.put("ref_1", b"data", content_type="text/plain", filename="a.txt")
    store.delete("ref_1")
    assert store.get("ref_1") is None


def test_local_store_delete_of_missing_ref_does_not_raise(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    store.delete("never-written")  # must not raise


def test_local_store_ref_is_not_used_verbatim_as_a_path_no_traversal(tmp_path):
    """A ref containing path-traversal-shaped segments must never escape the
    store's own directory -- LocalBlobStore hashes every ref into an opaque
    directory name, so this is true by construction, not by input validation."""
    store = LocalBlobStore(str(tmp_path))
    malicious_ref = "../../../../etc/passwd"
    store.put(malicious_ref, b"payload", content_type="text/plain", filename="x.txt")

    # nothing was written outside the store's own directory
    escaped_path = (tmp_path / ".." / ".." / ".." / ".." / "etc" / "passwd").resolve()
    assert not escaped_path.exists() or escaped_path.read_bytes() != b"payload"

    # everything actually written stays inside tmp_path
    written_files = list(tmp_path.rglob("*"))
    assert all(str(f.resolve()).startswith(str(tmp_path.resolve())) for f in written_files)

    # the store can still read back what it wrote, keyed by the same ref
    assert store.get(malicious_ref).data == b"payload"


def test_local_store_creates_parent_directories(tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    store = LocalBlobStore(str(nested))
    store.put("ref_1", b"data", content_type="text/plain", filename="a.txt")
    assert store.get("ref_1").data == b"data"


def test_local_store_different_refs_are_isolated(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    store.put("ref_a", b"AAAA", content_type="text/plain", filename="a.txt")
    store.put("ref_b", b"BBBB", content_type="text/plain", filename="b.txt")
    assert store.get("ref_a").data == b"AAAA"
    assert store.get("ref_b").data == b"BBBB"


# ---- S3BlobStore --------------------------------------------------------------------------------

class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.put_calls: list[dict] = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs

    def get_object(self, **kwargs):
        from botocore.exceptions import ClientError

        obj = self.objects.get(kwargs["Key"])
        if obj is None:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        import io

        return {"Body": io.BytesIO(obj["Body"]), "ContentType": obj.get("ContentType"), "Metadata": obj.get("Metadata", {})}

    def delete_object(self, **kwargs):
        self.objects.pop(kwargs["Key"], None)


def _store(monkeypatch, *, bucket="promise-documents", key_prefix="documents/") -> tuple[S3BlobStore, _FakeS3Client]:
    import boto3

    client = _FakeS3Client()
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: client)
    return S3BlobStore(bucket=bucket, region="us-east-1", key_prefix=key_prefix), client


def test_s3_store_put_uses_the_correct_object_key(monkeypatch):
    store, client = _store(monkeypatch, key_prefix="documents/")
    store.put("document:ws_1:doc_1", b"data", content_type="text/plain", filename="a.txt")
    assert client.put_calls[0]["Key"] == "documents/document:ws_1:doc_1"


def test_s3_store_put_writes_exact_body_bytes_and_content_type(monkeypatch):
    store, client = _store(monkeypatch)
    data = b"\x00\x01exact bytes"
    store.put("ref_1", data, content_type="application/pdf", filename="report.pdf")

    call = client.put_calls[0]
    assert call["Body"] == data
    assert call["ContentType"] == "application/pdf"
    assert call["Metadata"] == {"filename": "report.pdf", "size_bytes": str(len(data))}


def test_s3_store_never_sets_a_public_acl(monkeypatch):
    store, client = _store(monkeypatch)
    store.put("ref_1", b"data", content_type="text/plain", filename="a.txt")
    call = client.put_calls[0]
    assert "ACL" not in call


def test_s3_store_get_returns_exact_bytes_and_metadata(monkeypatch):
    store, client = _store(monkeypatch)
    data = b"round trip me"
    store.put("ref_1", data, content_type="text/plain", filename="a.txt")

    blob = store.get("ref_1")
    assert blob.data == data
    assert blob.content_type == "text/plain"
    assert blob.filename == "a.txt"
    assert blob.size_bytes == len(data)


def test_s3_store_get_missing_key_returns_none(monkeypatch):
    store, _client = _store(monkeypatch)
    assert store.get("never-written") is None


def test_s3_store_delete_removes_the_object(monkeypatch):
    store, client = _store(monkeypatch)
    store.put("ref_1", b"data", content_type="text/plain", filename="a.txt")
    store.delete("ref_1")
    assert store.get("ref_1") is None


def test_s3_store_no_bucket_or_region_hard_coded(monkeypatch):
    """The store only ever uses the bucket/region/prefix it was constructed
    with -- never a literal bucket name embedded in the implementation."""
    import inspect

    from promise_shared.blobs import s3_blob_store

    source = inspect.getsource(s3_blob_store)
    assert "promise-documents" not in source
    assert "us-east-1" not in source


def test_s3_store_ownership_is_enforced_via_workspace_baked_refs(monkeypatch):
    """S3BlobStore itself has no notion of "ownership" -- it just stores
    whatever ref it's given. Isolation comes from `blob_ref()` always baking
    workspace_id into the ref centrally (see the tests above), so an S3 key
    for one workspace's document can never collide with another's."""
    store, client = _store(monkeypatch)
    ref_a = blob_ref("ws_a", "doc_1")
    ref_b = blob_ref("ws_b", "doc_1")
    store.put(ref_a, b"workspace a's document", content_type="text/plain", filename="a.txt")
    store.put(ref_b, b"workspace b's document", content_type="text/plain", filename="b.txt")

    assert store.get(ref_a).data == b"workspace a's document"
    assert store.get(ref_b).data == b"workspace b's document"
