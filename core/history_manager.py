"""History manager (sections 30-33, 60).

ALL transactions live in ONE file: <workspace>/.ai-patch-manager/history.json
(JSON list + byte-exact filesystem backups under backups/). This single file
makes the history readable and lets us revert (undo) / re-apply (redo) any
transaction later.

Supports: list, view, diff between versions, undo, redo.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

from core import file_manager
from core.backup_manager import BackupManager
from core.diff_engine import unified_diff
from models.change import sha256_text
from models.transaction import Transaction


class HistoryError(Exception):
    pass


class HistoryManager:
    def __init__(self, history_file: str, backups_root: str):
        # history_file: path to a single JSON file OR a legacy directory
        if os.path.isdir(history_file):
            legacy = history_file
            history_file = os.path.join(legacy, "..", "history.json")
            self._migrate_legacy(legacy, history_file)
        self.history_file = os.path.abspath(history_file)
        self.backup_mgr = BackupManager(backups_root)
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)

    # -- persistence (single JSON document) ------------------------------------
    def _load_all(self) -> List[dict]:
        if not os.path.isfile(self.history_file):
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        if isinstance(data, dict):
            data = data.get("transactions", [])
        return list(data)

    def _save_all(self, records: List[dict]) -> None:
        tmp = self.history_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"transactions": records}, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.history_file)

    @staticmethod
    def _migrate_legacy(legacy_dir: str, new_path: str) -> None:
        if os.path.isfile(new_path) or not os.path.isdir(legacy_dir):
            return
        records = []
        for name in sorted(os.listdir(legacy_dir)):
            if name.endswith(".json"):
                try:
                    with open(os.path.join(legacy_dir, name), "r",
                              encoding="utf-8") as fh:
                        records.append(json.load(fh))
                except (OSError, ValueError):
                    continue
        if records:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            with open(new_path, "w", encoding="utf-8") as fh:
                json.dump({"transactions": records}, fh,
                          ensure_ascii=False, indent=2)

    def record(self, tx: Transaction) -> str:
        records = [r for r in self._load_all() if r.get("id") != tx.id]
        records.append(tx.to_json_dict())
        self._save_all(records)
        return self.history_file

    # -- baseline snapshot (created when the workspace is connected) ---------
    def has_baseline(self) -> bool:
        return any(r.get("kind") == "baseline" for r in self._load_all())

    def ensure_baseline(self, project_root: str, files: List[str]) -> Optional[Transaction]:
        """Create the FIRST history entry: a byte-exact snapshot of every
        scanned file. Guarantees 'Diff vs Previous Version' always has a real
        previous state to compare against - even for the very first patch."""
        if self.has_baseline():
            return None
        from models.change import sha256_text
        tx = Transaction(project=os.path.basename(str(project_root).rstrip("/\\")),
                         task="", summary="workspace baseline (auto-backup on open)",
                         status="baseline")
        snap = self.backup_mgr.create_snapshot_dir(tx.id)
        tx.backup_dir = snap
        for rel in files:
            abs_path = os.path.join(str(project_root), rel)
            if not os.path.isfile(abs_path):
                continue
            rec = FileRecord(path=rel, operation="baseline", change_ids=[],
                             existed_before=True)
            rec.backup_file = self.backup_mgr.store_original(snap, project_root, rel)
            if rec.backup_file:
                try:
                    with open(os.path.join(snap, rec.backup_file), "rb") as fh:
                        rec.old_hash = sha256_text(fh.read().decode("utf-8", "replace"))
                except OSError:
                    pass
                tx.files.append(rec)
        self.record(tx)
        return tx

    def baseline(self) -> Optional[Transaction]:
        for r in self._load_all():
            if r.get("kind") == "baseline":
                try:
                    return Transaction.from_dict(r)
                except (KeyError, ValueError, TypeError):
                    return None
        return None

    def list_transactions(self) -> List[Transaction]:
        items = []
        for d in self._load_all():
            try:
                items.append(Transaction.from_dict(d))
            except (KeyError, ValueError, TypeError):
                continue
        return sorted(items, key=lambda t: t.timestamp, reverse=True)

    def get(self, tx_id: str) -> Optional[Transaction]:
        for d in self._load_all():
            if d.get("id") == tx_id:
                try:
                    return Transaction.from_dict(d)
                except (KeyError, ValueError, TypeError):
                    return None
        return None

    # -- diff between history versions (section 33) --------------------------
    def diff_between(self, project_root: str, earlier: Transaction,
                     later: Transaction) -> str:
        """Diff all files touched by either transaction.

        'earlier' contributes its PRE-apply state, 'later' its POST-apply
        state - so even the very first patch shows a real before/after diff
        (its pre-apply snapshot was captured automatically)."""
        paths = sorted({f.path for f in earlier.files} | {f.path for f in later.files})
        out: List[str] = []
        for rel in paths:
            a = self._content_at(project_root, earlier, rel, "before")
            b = self._content_at(project_root, later, rel, "after")
            if file_manager.is_binary_file(file_manager.safe_join(project_root, rel)):
                ha = next((f.old_hash for f in earlier.files if f.path == rel), "")
                hb = next((f.new_hash for f in later.files if f.path == rel), "")
                same = " (identical)" if ha and ha == hb else ""
                out.append(f"--- a/{rel}\n+++ b/{rel}\n"
                           f"Binary file - no textual diff available. "
                           f"hash before={ha[:12] or '-'} after={hb[:12] or '-'}{same}")
                continue
            d = unified_diff(a or "", b or "", rel)
            out.append(d if d else f"--- a/{rel}\n+++ b/{rel}\n[no differences]")
        return "\n".join(out)

    def diff_baseline_vs_current(self, project_root: str,
                                 current_files: Optional[List[str]] = None) -> str:
        """Diff the workspace baseline snapshot against the files as they are
        on disk NOW (i.e. everything that changed since the workspace was
        connected). Includes files created / deleted since the baseline."""
        base = self.baseline()
        if base is None or not base.backup_dir:
            return "[no baseline snapshot available]"

        def read_baseline(rec) -> Optional[str]:
            if not rec or not rec.backup_file:
                return None
            path = os.path.join(base.backup_dir, rec.backup_file)
            if not os.path.isfile(path):
                return None
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()

        base_recs = {f.path: f for f in base.files}
        paths = set(base_recs)
        for tx in self.list_transactions():          # files created later
            if tx.status != "baseline":
                paths.update(f.path for f in tx.files)
                paths.update(f.new_path for f in tx.files
                             if getattr(f, "new_path", None))
        paths.update(current_files or [])             # incl. rename targets
        out: List[str] = []
        for rel in sorted(paths):
            try:
                abs_path = file_manager.safe_join(project_root, rel)
            except Exception:
                continue
            exists_now = os.path.isfile(abs_path)
            if exists_now and file_manager.is_binary_file(abs_path):
                rec = base_recs.get(rel)
                ha = rec.old_hash if rec else ""
                hb = file_manager.hash_file_or_text(abs_path) or ""
                if ha != hb:
                    out.append(f"--- a/{rel}\n+++ b/{rel}\nBinary file changed - no "
                               f"textual diff available.")
                continue
            a = read_baseline(base_recs.get(rel)) or ""
            b = (file_manager.read_text(abs_path) or "") if exists_now else ""
            d = unified_diff(a, b, rel)
            if d:
                out.append(d)
        return "\n".join(out)

    def _content_at(self, project_root: str, tx: Transaction, rel: str,
                    state: str = "after") -> Optional[str]:
        """Return the content of *rel* as it was at transaction *tx*.

        state='before' -> pre-apply snapshot (baseline for new files)
        state='after'  -> post-apply redo copy (falls back to pre-apply when
                          this transaction never touched the file).
        """
        rec = next((f for f in tx.files if f.path == rel), None)
        snap = tx.backup_dir or ""

        def read(path: str) -> Optional[str]:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()

        new_src = ""
        orig_src = ""
        if rec is not None and snap:
            cand = os.path.join(snap, self.backup_mgr.backup_name(rec.path) + ".new")
            if rec.new_hash and os.path.isfile(cand):
                new_src = cand
            cand = os.path.join(snap, rec.backup_file) if rec.backup_file else ""
            if cand and os.path.isfile(cand):
                orig_src = cand

        if state == "before":
            if orig_src:
                return read(orig_src)
            if rec is not None and not rec.existed_before:
                return ""                      # file did not exist yet
        else:  # 'after'
            if new_src:
                return read(new_src)
            if rec is None and snap:
                # untouched by this transaction: use an older baseline snapshot
                base = self.baseline()
                if base is not None and base.backup_dir:
                    cand = os.path.join(base.backup_dir,
                                        self.backup_mgr.backup_name(rel) + ".orig")
                    if os.path.isfile(cand):
                        return read(cand)
            if orig_src:
                return read(orig_src)          # unchanged through this tx
            if rec is not None and rec.operation == "delete":
                pass                           # deleted -> fall through to disk
        return file_manager.read_text(file_manager.safe_join(project_root, rel))

    # -- undo / redo (sections 32, 60) ----------------------------------------
    def _set_status(self, tx: Transaction) -> None:
        records = self._load_all()
        for r in records:
            if r.get("id") == tx.id:
                r["status"] = tx.status
                break
        else:
            records.append(tx.to_json_dict())
        self._save_all(records)

    def undo(self, project_root: str, tx_id: str) -> Transaction:
        """Revert a transaction: restore every touched file from its backup."""
        tx = self.get(tx_id)
        if tx is None:
            raise HistoryError(f"unknown transaction '{tx_id}'")
        if tx.status == "baseline":
            raise HistoryError("the workspace baseline snapshot cannot be undone")
        if tx.status != "applied":
            raise HistoryError(f"transaction {tx.id} is already {tx.status}")
        if not tx.backup_dir or not os.path.isdir(tx.backup_dir):
            raise HistoryError("backup snapshot missing - cannot undo safely")

        # restore in reverse order; verify integrity afterwards (section 60)
        for rec in reversed(tx.files):
            restored_hash = self.backup_mgr.restore(
                tx.backup_dir, project_root, rec.path, rec.existed_before)
            if rec.existed_before and restored_hash != rec.old_hash:
                raise HistoryError(
                    f"integrity check failed while restoring {rec.path}")
        tx.status = "rolled_back"
        self._set_status(tx)
        return tx

    def redo(self, project_root: str, tx_id: str) -> Transaction:
        """Re-apply an undone transaction using the post-apply 'redo' copies
        that were captured at apply time (byte-exact, works for binaries)."""
        tx = self.get(tx_id)
        if tx is None:
            raise HistoryError(f"unknown transaction '{tx_id}'")
        if tx.status != "rolled_back":
            raise HistoryError(f"transaction {tx.id} is not rolled back "
                               f"(status: {tx.status})")
        if not tx.backup_dir or not os.path.isdir(tx.backup_dir):
            raise HistoryError("backup snapshot missing - cannot redo safely")
        for rec in tx.files:
            if rec.operation == "delete" or not rec.new_hash:
                continue
            src = os.path.join(tx.backup_dir,
                               self.backup_mgr.backup_name(rec.path) + ".new")
            if not os.path.isfile(src):
                raise HistoryError(f"redo copy missing for {rec.path} - "
                                   "cannot redo this transaction")
        # all copies present -> restore them
        for rec in tx.files:
            if rec.operation == "delete":
                target = file_manager.safe_join(project_root, rec.path)
                file_manager.delete_file(target)
                continue
            if not rec.new_hash:
                continue
            src = os.path.join(tx.backup_dir,
                               self.backup_mgr.backup_name(rec.path) + ".new")
            dst = file_manager.safe_join(project_root, rec.path)
            file_manager.copy_file(src, dst)
            if self.backup_mgr._hash_of_file(dst) != rec.new_hash:
                raise HistoryError(
                    f"integrity check failed while redoing {rec.path}")
        tx.status = "applied"
        self._set_status(tx)
        return tx


# small helper attached to FileRecord without touching the model file logic
def _status_of(self) -> str:  # pragma: no cover - trivial
    return "new" if not self.existed_before else "existing"


from models.transaction import FileRecord  # noqa: E402

FileRecord.status_of = _status_of  # type: ignore[attr-defined]
