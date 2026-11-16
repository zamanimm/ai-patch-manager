"""Transactional Apply engine (sections 29, 51, 53, 57, 63).

Flow per Apply:
    Dry Run -> Backup all -> Apply changes one by one
                          -> on failure: ROLLBACK everything

The whole batch is one transaction; Undo restores it from the backup.
"""
from __future__ import annotations

import os
import stat
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core import file_manager
from core.backup_manager import BackupManager
from core.patch_engine import PatchError, compute_file_result, merge_file_changes, plan_file_operations
from models.change import Change
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
        for c in changes:
            if c.selected and c.operation == "rename" and c.new_path:
                dest = self._join(c.new_path)
                if os.path.exists(dest):           # may have appeared since validation
                    problems.append(f"{c.id}: rename target already exists: {c.new_path}")
        groups = plan_file_operations([c for c in changes if c.selected])
        for rel, chs in groups.items():
            original = file_manager.read_text(self._join(rel))
            ops = {c.operation for c in chs}
            if original is None and "create" not in ops:
                problems.append(f"{rel}: file does not exist")
                continue
            region_ops = [c for c in chs if c.is_region_op]
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
        tx.change_records = [c.to_history_dict() for c in selected]
        tx.apply_settings = {
            name: getattr(self.settings, name)
            for name in ("exact_match", "hash_validation", "context_match",
                         "fuzzy_match", "fuzzy_threshold", "external_paths")
            if hasattr(self.settings, name)
        }
        report.transaction = tx
        snapshot_dir = self.backup_mgr.create_snapshot_dir(tx.id)
        tx.backup_dir = snapshot_dir
        # Even when the user disables persistent backups, Apply still needs an
        # ephemeral raw snapshot so a mid-batch failure can be rolled back.
        runtime_snapshot_dir = snapshot_dir
        temporary_snapshot_dir = ""
        if not self.settings.backup_before_apply:
            temporary_snapshot_dir = tempfile.mkdtemp(prefix="acp-apply-guard-")
            runtime_snapshot_dir = temporary_snapshot_dir

        groups = plan_file_operations(selected)

        # ---- BACKUP ALL (section 28: backup happens before any write) ------
        originals = {}
        try:
            for rel in groups:
                content = file_manager.read_text(self._join(rel))
                originals[rel] = content
                old_hash = file_manager.hash_file_or_text(self._join(rel)) if content is not None else ""
                if content is not None and not old_hash:
                    raise OSError(f"could not hash original file before backup: {rel}")
                old_mode = (stat.S_IMODE(os.stat(self._join(rel)).st_mode)
                            if content is not None else None)
                rec = FileRecord(
                    path=rel,
                    operation=",".join(sorted({c.operation for c in groups[rel]})),
                    change_ids=[c.id for c in groups[rel]],
                    old_hash=old_hash,
                    existed_before=content is not None,
                    hash_format="raw-sha256",
                    old_mode=old_mode,
                )
                if content is not None:
                    rec.backup_file = self.backup_mgr.store_original(
                        runtime_snapshot_dir, self.project_root, rel,
                        allow_external=self.settings.external_paths)
                rec.current_path = rel
                rec.current_hash = old_hash
                rec.current_exists = content is not None
                tx.files.append(rec)
            self.log(f"[{tx.id}] backup OK ({len(groups)} files)")
        except OSError as exc:
            report.error = f"Backup failed, nothing was modified: {exc}"
            if temporary_snapshot_dir:
                shutil.rmtree(temporary_snapshot_dir, ignore_errors=True)
            return report

        # ---- APPLY ONE BY ONE; ANY FAILURE ROLLS THE BATCH BACK -------------
        try:
            for rel, chs in groups.items():
                abs_path = self._join(rel)
                original = originals[rel] or ""
                # File-level operations must not decode/rewrite binary data.
                # A rename-only operation moves the original bytes unchanged.
                if any(c.operation == "delete" for c in chs):
                    if os.path.isfile(abs_path):
                        file_manager.delete_file(abs_path)
                elif any(c.operation != "rename" for c in chs):
                    new_content = self._compute_new(chs, original)
                    file_manager.write_text(abs_path, new_content)
                rec = next(r for r in tx.files if r.path == rel)
                rec.new_hash = "" if any(c.operation == "delete" for c in chs) \
                    else file_manager.hash_file_or_text(abs_path)
                if not any(c.operation == "delete" for c in chs) and not rec.new_hash:
                    raise OSError(f"could not hash updated file after write: {rel}")
                rec.current_path = rel
                rec.current_hash = rec.new_hash
                rec.current_exists = not any(c.operation == "delete" for c in chs)
                rec.new_mode = (stat.S_IMODE(os.stat(abs_path).st_mode)
                                if rec.current_exists else None)
                rec.current_mode = rec.new_mode
                # Store the most compact safe Redo representation. The immutable
                # original snapshot remains the trusted base; a byte delta is used
                # only when it is materially smaller than a full .new snapshot.
                if self.settings.backup_before_apply:
                    original_snapshot = (os.path.join(snapshot_dir, rec.backup_file)
                                         if rec.backup_file else "")
                    if rec.existed_before and not original_snapshot:
                        raise OSError(f"original backup was not created for {rel}")
                    strategy, artifact = self.backup_mgr.store_redo_artifact(
                        snapshot_dir, rel, original_snapshot, abs_path,
                        rec.old_hash, rec.new_hash, rec.old_mode, rec.new_mode)
                    rec.redo_strategy = strategy
                    rec.redo_artifact_file = artifact if strategy == "delta" else ""
                else:
                    rec.redo_strategy = "unavailable"
                    rec.redo_artifact_file = ""
                for c in chs:
                    c.status = "applied"
                    report.applied_changes.append(c.id)
                    self.log(f"[{tx.id}] {c.id} apply OK -> {rel}")

            # renames happen after all content operations
            for ch in selected:
                if ch.operation == "rename" and ch.new_path:
                    file_manager.rename_file(self._join(ch.path),
                                             self._join(ch.new_path))
                    rec = next((r for r in tx.files if r.path == ch.path), None)
                    if rec is not None:
                        rec.new_path = ch.new_path      # undo / redo / rollback need to know where it went
                        rec.current_path = ch.new_path
                    self.log(f"[{tx.id}] {ch.id} renamed {ch.path} -> {ch.new_path}")
        except (PatchError, OSError) as exc:
            self.log(f"[{tx.id}] FAILED: {exc} - rolling back")
            rollback_errors = self._rollback(runtime_snapshot_dir, tx)
            tx.status = "failed" if not rollback_errors else "rollback_failed"
            report.rolled_back = not rollback_errors
            if rollback_errors:
                report.error = (
                    f"CRITICAL: Apply failed and rollback was incomplete:\n{exc}\n\n"
                    + "\n".join(rollback_errors)
                    + f"\n\nRecovery snapshots were retained at: {runtime_snapshot_dir}")
                if temporary_snapshot_dir:
                    tx.backup_dir = runtime_snapshot_dir
            else:
                report.error = f"Apply failed, transaction rolled back:\n{exc}"
            for c in selected:
                if c.status != "applied":
                    c.status = "failed"
            if temporary_snapshot_dir and not rollback_errors:
                for rec in tx.files:
                    rec.backup_file = ""
                shutil.rmtree(temporary_snapshot_dir, ignore_errors=True)
            return report

        if temporary_snapshot_dir:
            # The ephemeral guard is only for failures during Apply. It must
            # not be referenced by the persisted transaction history.
            for rec in tx.files:
                rec.backup_file = ""
            shutil.rmtree(temporary_snapshot_dir, ignore_errors=True)

        tx.status = "applied"
        report.ok = True
        report.details = [f"{f.path} ({f.operation})" for f in tx.files]
        return report

    # -- content computation ---------------------------------------------------
    def _compute_new(self, chs: List[Change], original: str) -> str:
        return compute_file_result(original, chs, self.settings)

    def _rollback(self, snapshot_dir: str, tx: Transaction) -> List[str]:
        """Restore every touched path and report any incomplete recovery."""
        errors = []
        for rec in reversed(tx.files):
            try:
                if rec.new_path:                        # a rename already happened: move it back first
                    renamed = self._join(rec.new_path)
                    if os.path.isfile(renamed):
                        file_manager.delete_file(renamed)
                self.backup_mgr.restore(snapshot_dir, self.project_root,
                                        rec.path, rec.existed_before,
                                        allow_external=self.settings.external_paths)
                self.log(f"[{tx.id}] rollback restored {rec.path}")
            except Exception as exc:
                message = f"{rec.path}: {exc}"
                errors.append(message)
                self.log(f"[{tx.id}] ROLLBACK ERROR {message}")
        return errors
