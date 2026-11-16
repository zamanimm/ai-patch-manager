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
    new_path: str = ""        # rename only: where the file was moved to (relative to the project root)
    hash_format: str = "legacy-text"  # raw-sha256 for new records; default preserves old history semantics
    current_path: str = ""            # current workspace location after partial rollback/redo
    current_hash: str = ""            # expected recorded hash at current_path
    current_exists: Optional[bool] = None
    current_backup_file: str = ""     # exact snapshot of the current partial state
    old_mode: Optional[int] = None
    new_mode: Optional[int] = None
    current_mode: Optional[int] = None
    # How transaction-level Redo recreates the post-Apply bytes. New records
    # prefer a compact reversible delta; legacy records default to snapshot.
    redo_strategy: str = "snapshot"  # snapshot | delta | original | delete | unavailable
    redo_artifact_file: str = ""      # delta artifact filename, when redo_strategy == delta


@dataclass
class Transaction:
    id: str = field(default_factory=lambda: "tx-" + uuid.uuid4().hex[:8])
    timestamp: float = field(default_factory=time.time)
    project: str = ""
    task: str = ""
    summary: str = ""
    status: str = "applied"          # applied | partially_rolled_back | rolled_back | failed | rollback_failed
    files: List[FileRecord] = field(default_factory=list)
    backup_dir: str = ""             # absolute path of on-disk snapshot
    change_records: List[dict] = field(default_factory=list)
    apply_settings: Dict[str, object] = field(default_factory=dict)
    rolled_back_change_ids: List[str] = field(default_factory=list)
    change_rollback_events: List[dict] = field(default_factory=list)
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
            "change_records": self.change_records,
            "apply_settings": self.apply_settings,
            "rolled_back_change_ids": list(self.rolled_back_change_ids),
            "change_rollback_events": list(self.change_rollback_events),
            "files": [
                {
                    "path": f.path,
                    "operation": f.operation,
                    "change_ids": f.change_ids,
                    "old_hash": f.old_hash,
                    "new_hash": f.new_hash,
                    "existed_before": f.existed_before,
                    "backup_file": f.backup_file,
                    "new_path": f.new_path,
                    "hash_format": f.hash_format,
                    "current_path": f.current_path,
                    "current_hash": f.current_hash,
                    "current_exists": f.current_exists,
                    "current_backup_file": f.current_backup_file,
                    "old_mode": f.old_mode,
                    "new_mode": f.new_mode,
                    "current_mode": f.current_mode,
                    "redo_strategy": f.redo_strategy,
                    "redo_artifact_file": f.redo_artifact_file,
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
            change_records=list(d.get("change_records") or []),
            apply_settings=dict(d.get("apply_settings") or {}),
            rolled_back_change_ids=list(d.get("rolled_back_change_ids") or []),
            change_rollback_events=list(d.get("change_rollback_events") or []),
        )
        for f in d.get("files", []):
            tx.files.append(FileRecord(
                path=f["path"], operation=f["operation"],
                change_ids=f.get("change_ids", []),
                old_hash=f.get("old_hash", ""), new_hash=f.get("new_hash", ""),
                existed_before=f.get("existed_before", True),
                backup_file=f.get("backup_file", ""),
                new_path=f.get("new_path", ""),
                hash_format=f.get("hash_format", "legacy-text"),
                current_path=f.get("current_path", ""),
                current_hash=f.get("current_hash", ""),
                current_exists=f.get("current_exists"),
                current_backup_file=f.get("current_backup_file", ""),
                old_mode=f.get("old_mode"),
                new_mode=f.get("new_mode"),
                current_mode=f.get("current_mode"),
                redo_strategy=f.get("redo_strategy", "snapshot"),
                redo_artifact_file=f.get("redo_artifact_file", ""),
            ))
        return tx

    @staticmethod
    def load(path: str) -> "Transaction":
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return Transaction.from_dict(d)
