from __future__ import annotations

import json
import stat
from pathlib import Path
from threading import RLock
from typing import Any


class LocalSecretStore:
    """File-backed secret store for local development and tests.

    One JSON file per secret, named after a filesystem-safe hash of its `ref`
    rather than the ref itself (a ref can embed a workspace/account id; the
    hash keeps that out of a filename an OS/backup tool might index or log).
    Restricts the directory and each file to owner-only permissions on POSIX.

    Not for production use -- no encryption at rest beyond the filesystem's
    own, no access auditing, no rotation. `SECRET_STORE_BACKEND=aws` is the
    production path (`AwsSecretsManagerStore`); this exists so local dev/tests
    never need real AWS credentials and never accidentally write a secret into
    a normal (committed-by-mistake) data file -- see `LOCAL_SECRETS_DIR` /
    `.gitignore`'s existing `data/` entry.
    """

    def __init__(self, data_dir: str = "./data/secrets") -> None:
        self.path = Path(data_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            self.path.chmod(stat.S_IRWXU)  # 0700: owner rwx only
        except OSError:
            pass  # best-effort on platforms/filesystems that don't support POSIX perms
        self._lock = RLock()

    @staticmethod
    def _filename(ref: str) -> str:
        import hashlib

        return hashlib.sha256(ref.encode("utf-8")).hexdigest() + ".json"

    def _file(self, ref: str) -> Path:
        return self.path / self._filename(ref)

    def get_secret(self, ref: str) -> dict[str, Any] | None:
        with self._lock:
            file = self._file(ref)
            if not file.exists():
                return None
            return json.loads(file.read_text(encoding="utf-8"))

    def put_secret(self, ref: str, value: dict[str, Any]) -> None:
        with self._lock:
            file = self._file(ref)
            file.write_text(json.dumps(value), encoding="utf-8")
            try:
                file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600: owner rw only
            except OSError:
                pass

    def delete_secret(self, ref: str) -> None:
        with self._lock:
            file = self._file(ref)
            file.unlink(missing_ok=True)
