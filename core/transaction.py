"""Transactional Apply engine (sections 29, 51, 53, 57, 63).

Flow per Apply:
    Dry Run -> Backup all -> Apply changes one by one
                          -> on failure: ROLLBACK everything

The whole batch is one transaction; Undo restores it from the backup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core import file_manager
from core.backup_manager import BackupManager
from core.patch_engine import PatchError, merge_file_changes, plan_file_operations
from models.change import Change, sha256_text
from models.transaction import FileRecord, Transaction


@dataclass
class ApplyReport:
    ok: bool = False
    transaction: Optional[Transaction] = None
    applied_changes: List[str] = field(default_factory=list)
    rolled_back: bool = False
    error: str = ""
    details: List[str] = field(default_factory=list)


class TransactionRunner:
    def __init__(self, project_root: str, settings, backups_root: str,
                 log: Optional[Callable[[str], None]] = None):
        self.project_root = project_root
        self.settings = settings
        self.backup_mgr = BackupManager(backups_root)
        self.log = log or (lambda msg: None)

    # -- helpers --------------------------------------------------------------
    def _join(self, rel: str) -> str:
        return file_manager.safe_join(self.project_root, rel,
                                      allow_external=self.settings.external_paths)

    # -- dry run (section 51) ------------------------------------------------
    def dry_run(self, changes: List[Change]) -> List[str]:
        """Check every selected change can be executed WITHOUT touching files."""
        problems: List[str] = []
        groups = plan_file_operations([c for c in changes if c.selected])
        for rel, chs in groups.items():
            original = file_manager.read_text(self._join(rel))
            ops = {c.operation for c in chs}
            if original is None and "create" not in ops:
                problems.append(f"{rel}: file does not exist")
                continue
            region_ops = [c for c in chs if c.operation in
                          ("replace", "insert_before", "insert_after")]
            try:
                if region_ops:
                    merge_file_changes(original or "", region_ops, self.settings)
            except PatchError as exc:
                problems.append(str(exc))
        return problems

    # -- apply ----------------------------------------------------------------
    def apply(self, changes: List[Change], task: str = "",
              summary: str = "") -> ApplyReport:
        report = ApplyReport()
        selected = [c for c in changes if c.selected and c.valid]
        if not selected:
            report.error = "No valid selected changes to apply."
            return report

        problems = self.dry_run(selected)
        if problems:
            report.error = "Dry run failed:\n" + "\n".join(problems)
            self.log("apply aborted: " + report.error)
            return report

        tx = Transaction(project=os.path.basename(self.project_root.rstrip("/\\")),
                         task=task, summary=summary)
        report.transaction = tx
        snapshot_dir = self.backup_mgr.create_snapshot_dir(tx.id)
        tx.backup_dir = snapshot_dir

        groups = plan_file_operations(selected)

        # ---- BACKUP ALL (section 28: backup happens before any write) ------
        originals = {}
        try:
            for rel in groups:
                content = file_manager.read_text(self._join(rel))
                originals[rel] = content
                rec = FileRecord(
                    path=rel,
                    operation=",".join(sorted({c.operation for c in groups[rel]})),
                    change_ids=[c.id for c in groups[rel]],
                    old_hash=sha256_text(content) if content is not None else "",
                    existed_before=content is not None,
                )
                if content is not None and self.settings.backup_before_apply:
                    rec.backup_file = self.backup_mgr.store_original(
                        snapshot_dir, self.project_root, rel)
                tx.files.append(rec)
            self.log(f"[{tx.id}] backup OK ({len(groups)} files)")
        except OSError as exc:
            report.error = f"Backup failed, nothing was modified: {exc}"
            return report

        # ---- APPLY ONE BY ONE; ANY FAILURE ROLLS THE BATCH BACK -------------
        try:
            for rel, chs in groups.items():
                abs_path = self._join(rel)
                original = originals[rel] or ""
                new_content = self._compute_new(chs, original)
                if any(c.operation == "delete" for c in chs):
                    if os.path.isfile(abs_path):
                        os.remove(abs_path)
                else:
                    file_manager.write_text(abs_path, new_content)
                rec = next(r for r in tx.files if r.path == rel)
                rec.new_hash = "" if any(c.operation == "delete" for c in chs) \
                    else sha256_text(new_content)
                # capture the post-apply state so REDO can restore it exactly
                if rec.new_hash and self.settings.backup_before_apply:
                    self._store_new_copy(snapshot_dir, rel, abs_path)
                for c in chs:
                    c.status = "applied"
                    report.applied_changes.append(c.id)
                    self.log(f"[{tx.id}] {c.id} apply OK -> {rel}")

            # renames happen after all content operations
            for ch in selected:
                if ch.operation == "rename" and ch.new_path:
                    file_manager.rename_file(self._join(ch.path),
                                             self._join(ch.new_path))
                    self.log(f"[{tx.id}] {ch.id} renamed {ch.path} -> {ch.new_path}")
        except (PatchError, OSError) as exc:
            self.log(f"[{tx.id}] FAILED: {exc} - rolling back")
            self._rollback(snapshot_dir, tx)
            tx.status = "failed"
            report.rolled_back = True
            report.error = f"Apply failed, transaction rolled back:\n{exc}"
            for c in selected:
                if c.status != "applied":
                    c.status = "failed"
            return report

        tx.status = "applied"
        report.ok = True
        report.details = [f"{f.path} ({f.operation})" for f in tx.files]
        return report

    # -- content computation ---------------------------------------------------
    def _compute_new(self, chs: List[Change], original: str) -> str:
        region_ops = [c for c in chs if c.operation in
                      ("replace", "insert_before", "insert_after")]
        text = merge_file_changes(original, region_ops, self.settings) if region_ops else original
        for c in chs:
            if c.operation == "create":
                text = (c.content or "") + "\n"
            elif c.operation == "append":
                sep = "" if text.endswith("\n") or text == "" else "\n"
                text = text + sep + (c.content or "") + "\n"
            elif c.operation == "prepend":
                text = (c.content or "") + "\n" + text
        return text

    def _rollback(self, snapshot_dir: str, tx: Transaction) -> None:
        for rec in reversed(tx.files):
            try:
                self.backup_mgr.restore(snapshot_dir, self.project_root,
                                        rec.path, rec.existed_before)
                self.log(f"[{tx.id}] rollback restored {rec.path}")
            except OSError as exc:  # pragma: no cover
                self.log(f"[{tx.id}] ROLLBACK ERROR {rec.path}: {exc}")

    def _store_new_copy(self, snapshot_dir: str, rel: str, abs_path: str) -> None:
        """Byte-exact copy of the post-apply file (enables redo)."""
        try:
            dst = os.path.join(snapshot_dir,
                               BackupManager.backup_name(rel) + ".new")
            file_manager.copy_file(abs_path, dst)
        except OSError as exc:
            self.log(f"could not store redo copy for {rel}: {exc}")
