"""Transaction model: one Apply = one transaction (sections 29, 63).

A transaction records every file it touched together with the pre-apply
content (kept in-memory for rollback) and the backup snapshot on disk.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FileRecord:
    path: str                 # relative to project root
    operation: str
    change_ids: List[str] = field(default_factory=list)
    old_hash: str = ""        # sha256 of content before apply ("" if file did not exist)
    new_hash: str = ""        # sha256 of content after apply
    existed_before: bool = True
    backup_file: str = ""     # filename inside the backup dir holding the original copy


@dataclass
class Transaction:
    id: str = field(default_factory=lambda: "tx-" + uuid.uuid4().hex[:8])
    timestamp: float = field(default_factory=time.time)
    project: str = ""
    task: str = ""
    summary: str = ""
    status: str = "applied"          # applied | rolled_back | failed
    files: List[FileRecord] = field(default_factory=list)
    backup_dir: str = ""             # absolute path of on-disk snapshot
    # in-memory originals keyed by relative path -> content string (or None if absent)
    _originals: Dict[str, Optional[str]] = field(default_factory=dict, repr=False)

    def human_time(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

    def to_json_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": "baseline" if self.status == "baseline" else "patch",
            "timestamp": self.timestamp,
            "project": self.project,
            "task": self.task,
            "summary": self.summary,
            "status": self.status,
            "backup_dir": self.backup_dir,
            "changes": sorted({cid for f in self.files for cid in f.change_ids}),
            "files": [
                {
                    "path": f.path,
                    "operation": f.operation,
                    "change_ids": f.change_ids,
                    "old_hash": f.old_hash,
                    "new_hash": f.new_hash,
                    "existed_before": f.existed_before,
                    "backup_file": f.backup_file,
                }
                for f in self.files
            ],
        }

    def save(self, history_dir: str) -> str:
        """Legacy helper: write this transaction into a directory of JSONs."""
        os.makedirs(history_dir, exist_ok=True)
        path = os.path.join(history_dir, f"{self.id}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_json_dict(), fh, ensure_ascii=False, indent=2)
        return path

    @staticmethod
    def from_dict(d: dict) -> "Transaction":
        tx = Transaction(
            id=d["id"], timestamp=d["timestamp"], project=d.get("project", ""),
            task=d.get("task", ""), summary=d.get("summary", ""),
            status=d.get("status", "applied"), backup_dir=d.get("backup_dir", ""),
        )
        for f in d.get("files", []):
            tx.files.append(FileRecord(
                path=f["path"], operation=f["operation"],
                change_ids=f.get("change_ids", []),
                old_hash=f.get("old_hash", ""), new_hash=f.get("new_hash", ""),
                existed_before=f.get("existed_before", True),
                backup_file=f.get("backup_file", ""),
            ))
        return tx

    @staticmethod
    def load(path: str) -> "Transaction":
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return Transaction.from_dict(d)
