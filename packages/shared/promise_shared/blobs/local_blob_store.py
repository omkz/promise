from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import BlobObject


class LocalBlobStore:
    """Filesystem-backed `DocumentBlobStore` for local development and tests.

    Layout: one directory per `ref`, named after a filesystem-safe hash of the
    ref rather than the ref (or the document id/filename inside it) verbatim
    -- the same reasoning `LocalSecretStore` uses for its own filenames, and
    the mechanism that makes path traversal impossible here: nothing the
    caller supplies (`ref`, `filename`) is ever interpolated into a path.

        data/documents/<sha256(ref)>/
            artifact       -- the exact bytes, verbatim
            metadata.json  -- {"content_type": ..., "filename": ..., "size_bytes": ...}

    Writes are atomic where practical: content is written to a temp file in
    the same directory and `os.replace()`d into place, so a reader never sees
    a partially-written artifact, and a crash mid-write leaves the previous
    artifact (or nothing) rather than a corrupt one. Not for production use --
    no encryption at rest beyond the filesystem's own, no versioning beyond
    "the last `put()` wins" (matching `EntityStore.put`'s own upsert
    semantics). `BLOB_STORE_BACKEND=s3` is the production path (`S3BlobStore`).
    """

    def __init__(self, data_dir: str = "./data/documents") -> None:
        self.path = Path(data_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def _dirname(ref: str) -> str:
        import hashlib

        return hashlib.sha256(ref.encode("utf-8")).hexdigest()

    def _ref_dir(self, ref: str) -> Path:
        return self.path / self._dirname(ref)

    def put(self, ref: str, data: bytes, *, content_type: str, filename: str) -> None:
        with self._lock:
            ref_dir = self._ref_dir(ref)
            ref_dir.mkdir(parents=True, exist_ok=True)

            artifact_path = ref_dir / "artifact"
            tmp_artifact = ref_dir / "artifact.tmp"
            tmp_artifact.write_bytes(data)
            os.replace(tmp_artifact, artifact_path)  # atomic on POSIX

            metadata = {"content_type": content_type, "filename": filename, "size_bytes": len(data)}
            metadata_path = ref_dir / "metadata.json"
            tmp_metadata = ref_dir / "metadata.json.tmp"
            tmp_metadata.write_text(json.dumps(metadata), encoding="utf-8")
            os.replace(tmp_metadata, metadata_path)

    def get(self, ref: str) -> BlobObject | None:
        from . import BlobObject

        with self._lock:
            ref_dir = self._ref_dir(ref)
            artifact_path = ref_dir / "artifact"
            metadata_path = ref_dir / "metadata.json"
            if not artifact_path.exists() or not metadata_path.exists():
                return None
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            data = artifact_path.read_bytes()
            return BlobObject(
                data=data, content_type=metadata["content_type"], filename=metadata["filename"],
                size_bytes=metadata.get("size_bytes", len(data)),
            )

    def delete(self, ref: str) -> None:
        with self._lock:
            ref_dir = self._ref_dir(ref)
            for name in ("artifact", "metadata.json", "artifact.tmp", "metadata.json.tmp"):
                (ref_dir / name).unlink(missing_ok=True)
            try:
                ref_dir.rmdir()
            except OSError:
                pass  # not empty (shouldn't happen) or already gone -- never fail delete on this
