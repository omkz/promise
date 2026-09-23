from __future__ import annotations

import json
from pathlib import Path
from threading import RLock


class LocalJsonEntityStore:
    """File-backed entity store for local development and tests.

    No AWS credentials required. Not for production use (no real
    concurrency control across processes), but the read/write contract
    matches DynamoEntityStore so the domain/app layers are storage-agnostic.
    """

    def __init__(self, data_dir: str = "./data") -> None:
        self.path = Path(data_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        self.file = self.path / "promise.json"
        self._lock = RLock()
        if not self.file.exists():
            self._write({})

    def _read(self) -> dict[str, list[dict]]:
        with self._lock:
            if not self.file.exists():
                return {}
            raw = self.file.read_text(encoding="utf-8").strip()
            return json.loads(raw) if raw else {}

    def _write(self, payload: dict[str, list[dict]]) -> None:
        with self._lock:
            self.file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def put(self, entity: str, item: dict) -> dict:
        if "id" not in item or "workspace_id" not in item:
            raise ValueError("entity rows must include 'id' and 'workspace_id'")
        with self._lock:
            data = self._read()
            rows = data.setdefault(entity, [])
            for i, row in enumerate(rows):
                if row.get("id") == item["id"]:
                    rows[i] = item
                    break
            else:
                rows.append(item)
            self._write(data)
        return item

    def get(self, entity: str, workspace_id: str, item_id: str) -> dict | None:
        rows = self._read().get(entity, [])
        return next(
            (r for r in rows if r.get("id") == item_id and r.get("workspace_id") == workspace_id),
            None,
        )

    def query(self, entity: str, workspace_id: str) -> list[dict]:
        rows = self._read().get(entity, [])
        return [r for r in rows if r.get("workspace_id") == workspace_id]

    def query_all(self, entity: str) -> list[dict]:
        return list(self._read().get(entity, []))

    def delete(self, entity: str, workspace_id: str, item_id: str) -> None:
        with self._lock:
            data = self._read()
            rows = data.get(entity, [])
            data[entity] = [
                r for r in rows if not (r.get("id") == item_id and r.get("workspace_id") == workspace_id)
            ]
            self._write(data)

    def clear(self) -> None:
        self._write({})
