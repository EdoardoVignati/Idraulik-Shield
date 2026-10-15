"""Approval queue for write actions, persisted as JSON in the state dir."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class ApprovalStore:
    def __init__(self, state_dir: str):
        self.path = Path(state_dir) / "approvals.json"

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2))
        tmp.replace(self.path)

    def add(self, tool: str, arguments: dict, reason: str = "") -> dict:
        items = self._load()
        for i in items:  # dedupe identical pending requests
            if i["status"] == "pending" and i["tool"] == tool and i["arguments"] == arguments:
                return i
        item = {"id": uuid.uuid4().hex[:6], "tool": tool, "arguments": arguments, "reason": reason,
                "status": "pending", "created": int(time.time()), "result": None}
        items.append(item)
        self._save(items)
        return item

    def list(self, status: str | None = None) -> list[dict]:
        return [i for i in self._load() if status in (None, i["status"])]

    def update(self, id_: str, **fields) -> dict | None:
        items = self._load()
        for i in items:
            if i["id"] == id_:
                i.update(fields)
                self._save(items)
                return i
        return None
