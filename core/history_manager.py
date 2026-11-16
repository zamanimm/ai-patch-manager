"""Atomic JSON transaction history (no append-only journal or in-memory index).

On first use, legacy ``history.jsonl`` is merged into ``history.json`` with the
latest journal record winning for each transaction. After a successful atomic
write, the old journal is archived (never deleted), preventing stale replay.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from typing import List, Optional

from core import file_manager
from core.backup_manager import BackupManager
from core.diff_engine import unified_diff
from models.change import sha256_text
from models.transaction import FileRecord, Transaction


class HistoryError(Exception):
    pass


class HistoryManager:
    def _matches_record_hash(self, path: str, expected: str,
                             hash_format: str = "legacy-text") -> bool:
        """Verify raw hashes strictly; accept old text-normalized hashes."""
        if not expected or not os.path.isfile(path):
            return False
        try:
            raw_hash = self.backup_mgr._hash_of_file(path)
            if raw_hash == expected:
                return True
            if hash_format == "raw-sha256":
                return False
            legacy_text = file_manager.read_text(path)
            return legacy_text is not None and sha256_text(legacy_text) == expected
        except OSError:
            return False

    def __init__(self, history_file: str, backups_root: str):
        if os.path.isdir(history_file):
            legacy = history_file
            history_file = os.path.join(legacy, "..", "history.json")
            self._migrate_legacy(legacy, history_file)
        self.history_file = os.path.abspath(history_file)
        stem, _ext = os.path.splitext(self.history_file)
        self.journal_file = stem + ".jsonl"  # legacy input only
        self.backup_mgr = BackupManager(backups_root)
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        self._lock = threading.RLock()
        self._import_old_journal()

    @staticmethod
    def _migrate_legacy(legacy_dir: str, new_path: str) -> None:
        if os.path.isfile(new_path) or not os.path.isdir(legacy_dir):
            return
        records = []
        for name in sorted(os.listdir(legacy_dir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(legacy_dir, name), "r", encoding="utf-8") as fh:
                    item = json.load(fh)
                if isinstance(item, dict) and item.get("id"):
                    records.append(item)
            except (OSError, ValueError):
                continue
        if records:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            HistoryManager._write_json_atomic(new_path, records)

    @staticmethod
    def _write_json_atomic(path: str, records: list) -> None:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"transactions": records}, fh, ensure_ascii=False,
                          separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _load_all(self) -> List[dict]:
        if not os.path.isfile(self.history_file):
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, UnicodeError) as exc:
            # Never silently replace an unreadable history with an empty one.
            raise HistoryError(f"Could not read history: {exc}") from exc
        if isinstance(data, dict):
            data = data.get("transactions", [])
        if not isinstance(data, list):
            raise HistoryError("History JSON must contain a transactions list")
        return [r for r in data if isinstance(r, dict) and r.get("id")]

    def _import_old_journal(self) -> None:
        if not os.path.isfile(self.journal_file):
            return
        with self._lock:
            records = {r["id"]: r for r in self._load_all()}
            try:
                with open(self.journal_file, "rb") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            item = json.loads(line.decode("utf-8"))
                        except (ValueError, UnicodeError):
                            # A torn final line must not erase prior records.
                            continue
                        if isinstance(item, dict) and item.get("id"):
                            records[item["id"]] = item
            except OSError as exc:
                raise HistoryError(f"Could not migrate history journal: {exc}") from exc
            # Publish a complete JSON history BEFORE retiring the journal.
            self._write_json_atomic(self.history_file, list(records.values()))
            archive = self.journal_file + ".migrated-" + uuid.uuid4().hex[:8]
            try:
                os.replace(self.journal_file, archive)
            except OSError as exc:
                raise HistoryError(
                    f"History was saved, but the old journal could not be archived: {exc}"
                ) from exc

    def record(self, tx: Transaction) -> str:
        with self._lock:
            rows = self._load_all()
            payload = tx.to_json_dict()
            ids = {item["id"]: i for i, item in enumerate(rows)}
            if payload["id"] in ids:
                rows[ids[payload["id"]]] = payload
            else:
                rows.append(payload)
            self._write_json_atomic(self.history_file, rows)
        return self.history_file

    # -- baseline snapshot ---------------------------------------------------
    def has_baseline(self) -> bool:
        return any(r.get("kind") == "baseline" for r in self._load_all())

    def ensure_baseline(self, project_root: str,
                        files: List[str]) -> Optional[Transaction]:
        """Create the first byte-exact snapshot of every scanned file."""
        if self.has_baseline():
            return None
        tx = Transaction(project=os.path.basename(str(project_root).rstrip("/\\")),
                         task="", summary="workspace baseline (auto-backup on open)",
                         status="baseline")
        snap = self.backup_mgr.create_snapshot_dir(tx.id)
        tx.backup_dir = snap
        for rel in files:
            try:
                abs_path = file_manager.safe_join(project_root, rel)
                if not os.path.isfile(abs_path):
                    continue
                rec = FileRecord(path=rel, operation="baseline", change_ids=[],
                                 existed_before=True, hash_format="raw-sha256")
                rec.backup_file = self.backup_mgr.store_original(snap, project_root, rel)
                if rec.backup_file:
                    rec.old_hash = self.backup_mgr._hash_of_file(
                        os.path.join(snap, rec.backup_file))
                    tx.files.append(rec)
            except OSError:
                continue
        self.record(tx)
        return tx

    def baseline(self) -> Optional[Transaction]:
        for record in self._load_all():
            if record.get("kind") == "baseline":
                try:
                    return Transaction.from_dict(record)
                except (KeyError, ValueError, TypeError):
                    return None
        return None

    def _ordered_records(self) -> list:
        return sorted(self._load_all(),
                      key=lambda record: float(record.get("timestamp", 0)),
                      reverse=True)

    def transaction_count(self) -> int:
        return len(self._load_all())

    def list_transactions(self, limit: Optional[int] = None,
                          offset: int = 0) -> List[Transaction]:
        rows = self._ordered_records()[max(0, int(offset)):]
        if limit is not None:
            rows = rows[:max(0, int(limit))]
        items = []
        for row in rows:
            try:
                items.append(Transaction.from_dict(row))
            except (KeyError, ValueError, TypeError):
                continue
        return items

    def previous_transaction(self, tx_id: str) -> Optional[Transaction]:
        rows = self._ordered_records()
        for idx, row in enumerate(rows):
            if row.get("id") == tx_id and idx + 1 < len(rows):
                try:
                    return Transaction.from_dict(rows[idx + 1])
                except (KeyError, ValueError, TypeError):
                    return None
        return None

    def get(self, tx_id: str) -> Optional[Transaction]:
        for row in self._load_all():
            if row.get("id") == tx_id:
                try:
                    return Transaction.from_dict(row)
                except (KeyError, ValueError, TypeError):
                    return None
        return None

    # -- diff between history versions --------------------------------------
    def diff_between(self, project_root: str, earlier: Transaction,
                     later: Transaction) -> str:
        paths = sorted({f.path for f in earlier.files} | {f.path for f in later.files})
        out: List[str] = []
        for rel in paths:
            a = self._content_at(project_root, earlier, rel, "before")
            b = self._content_at(project_root, later, rel, "after")
            try:
                is_binary = file_manager.is_binary_file(
                    file_manager.safe_join(project_root, rel))
            except Exception:
                is_binary = False
            if is_binary:
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

    def baseline_text(self, rel: str) -> Optional[str]:
        base = self.baseline()
        if base is None or not base.backup_dir:
            return None
        rec = next((f for f in base.files if f.path == rel), None)
        if rec is None or not rec.backup_file:
            return ""
        path = os.path.join(base.backup_dir, rec.backup_file)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def diff_baseline_vs_current(self, project_root: str,
                                 current_files: Optional[List[str]] = None) -> str:
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
        for tx in self.list_transactions():
            if tx.status != "baseline":
                paths.update(f.path for f in tx.files)
                paths.update(f.new_path for f in tx.files
                             if getattr(f, "new_path", None))
        paths.update(current_files or [])
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
                legacy_match = False
                if rec is not None and rec.hash_format != "raw-sha256":
                    legacy_match = (sha256_text(file_manager.read_text(abs_path) or "") == ha)
                if ha != hb and not legacy_match:
                    out.append(f"--- a/{rel}\n+++ b/{rel}\nBinary file changed - no "
                               f"textual diff available.")
                continue
            a = read_baseline(base_recs.get(rel)) or ""
            b = (file_manager.read_text(abs_path) or "") if exists_now else ""
            d = unified_diff(a, b, rel)
            if d:
                out.append(d)
        return "\n".join(out)

    def file_versions(self, project_root: str, tx: Transaction, rel: str) -> list:
        rec = next((f for f in tx.files if f.path == rel), None)
        try:
            abs_path = file_manager.safe_join(project_root, rel)
            current = file_manager.read_text(abs_path)
        except Exception:
            current = None
        versions: list = []
        try:
            if tx.status == "baseline":
                versions.append(("Baseline snapshot (when the workspace was connected)",
                                 self._content_at(project_root, tx, rel, "before")))
            else:
                op = rec.operation if rec else ""
                gone_after = any(x in op for x in ("delete", "rename"))
                versions.append(("After this transaction",
                                 None if gone_after else
                                 self._content_at(project_root, tx, rel, "after")))
                existed = rec.existed_before if rec else True
                versions.append(("Before this transaction",
                                 self._content_at(project_root, tx, rel, "before")
                                 if existed else None))
        except OSError:
            pass
        versions.append(("Current workspace file", current))
        return versions

    def _content_at(self, project_root: str, tx: Transaction, rel: str,
                    state: str = "after") -> Optional[str]:
        rec = next((f for f in tx.files if f.path == rel), None)
        snap = tx.backup_dir or ""

        def read(path: str) -> Optional[str]:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()

        new_src = ""
        orig_src = ""
        if rec is not None and snap:
            cand = self.backup_mgr.snapshot_path(snap, rec.path, ".new")
            if rec.new_hash and os.path.isfile(cand):
                new_src = cand
            cand = os.path.join(snap, rec.backup_file) if rec.backup_file else ""
            if cand and os.path.isfile(cand):
                orig_src = cand

        if state == "before":
            if orig_src:
                return read(orig_src)
            if rec is not None and not rec.existed_before:
                return ""
        else:
            if new_src:
                return read(new_src)
            if rec is None and snap:
                base = self.baseline()
                if base is not None and base.backup_dir:
                    cand = self.backup_mgr.snapshot_path(base.backup_dir, rel, ".orig")
                    if os.path.isfile(cand):
                        return read(cand)
            if orig_src:
                return read(orig_src)
            if rec is not None and rec.operation == "delete":
                pass
        return file_manager.read_text(file_manager.safe_join(project_root, rel))

    # -- undo / redo ---------------------------------------------------------
    def _set_status(self, tx: Transaction) -> None:
        self.record(tx)

    def undo(self, project_root: str, tx_id: str) -> Transaction:
        tx = self.get(tx_id)
        if tx is None:
            raise HistoryError(f"unknown transaction '{tx_id}'")
        if tx.status == "baseline":
            raise HistoryError("the workspace baseline snapshot cannot be undone")
        if tx.status != "applied":
            raise HistoryError(f"transaction {tx.id} is already {tx.status}")
        if not tx.backup_dir or not os.path.isdir(tx.backup_dir):
            raise HistoryError("backup snapshot missing - cannot undo safely")

        for rec in tx.files:
            if rec.new_path:
                dest = file_manager.safe_join(project_root, rec.new_path)
                if os.path.exists(dest) and not self._matches_record_hash(
                        dest, rec.new_hash, rec.hash_format):
                    raise HistoryError(
                        f"{rec.new_path} was changed after it was renamed, so undo will not delete it. "
                        "Move or remove it yourself, then undo again.")
        for rec in reversed(tx.files):
            if rec.new_path:
                file_manager.delete_file(file_manager.safe_join(project_root, rec.new_path))
            restored_hash = self.backup_mgr.restore(
                tx.backup_dir, project_root, rec.path, rec.existed_before)
            target = file_manager.safe_join(project_root, rec.path)
            if rec.existed_before and not (
                    restored_hash == rec.old_hash or
                    self._matches_record_hash(target, rec.old_hash, rec.hash_format)):
                raise HistoryError(f"integrity check failed while restoring {rec.path}")
        tx.status = "rolled_back"
        self._set_status(tx)
        return tx

    def redo(self, project_root: str, tx_id: str) -> Transaction:
        tx = self.get(tx_id)
        if tx is None:
            raise HistoryError(f"unknown transaction '{tx_id}'")
        if tx.status != "rolled_back":
            raise HistoryError(f"transaction {tx.id} is not rolled back (status: {tx.status})")
        if not tx.backup_dir or not os.path.isdir(tx.backup_dir):
            raise HistoryError("backup snapshot missing - cannot redo safely")
        for rec in tx.files:
            if rec.operation == "delete" or not rec.new_hash:
                continue
            src = self.backup_mgr.snapshot_path(tx.backup_dir, rec.path, ".new")
            if not os.path.isfile(src):
                raise HistoryError(f"redo copy missing for {rec.path} - cannot redo this transaction")
        for rec in tx.files:
            if rec.new_path and os.path.exists(file_manager.safe_join(project_root, rec.new_path)):
                raise HistoryError(f"{rec.new_path} already exists - cannot redo the rename")
        for rec in tx.files:
            if rec.operation == "delete":
                file_manager.delete_file(file_manager.safe_join(project_root, rec.path))
                continue
            if not rec.new_hash:
                continue
            src = self.backup_mgr.snapshot_path(tx.backup_dir, rec.path, ".new")
            dst = file_manager.safe_join(project_root, rec.path)
            file_manager.copy_file(src, dst)
            if not self._matches_record_hash(dst, rec.new_hash, rec.hash_format):
                raise HistoryError(f"integrity check failed while redoing {rec.path}")
            if rec.new_path:
                file_manager.rename_file(dst, file_manager.safe_join(project_root, rec.new_path))
        tx.status = "applied"
        self._set_status(tx)
        return tx


# Compatibility helper retained for the history UI.
def _status_of(self) -> str:  # pragma: no cover - trivial
    return "new" if not self.existed_before else "existing"


FileRecord.status_of = _status_of  # type: ignore[attr-defined]
