"""Atomic JSON transaction history (no append-only journal or in-memory index).

On first use, legacy ``history.jsonl`` is merged into ``history.json`` with the
latest journal record winning for each transaction. After a successful atomic
write, the old journal is archived (never deleted), preventing stale replay.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
from typing import List, Optional

from core import file_manager
from core.backup_manager import BackupManager
from core.diff_engine import unified_diff
from core.patch_engine import compute_file_result
from models.change import Change, sha256_text
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
            later_rec = next((f for f in later.files if f.path == rel or
                              f.current_path == rel or f.new_path == rel), None)
            binary_path = ((later_rec.current_path or later_rec.new_path or rel)
                           if later_rec else rel)
            try:
                binary_abs = file_manager.safe_join(project_root, binary_path)
                is_binary = (os.path.isfile(binary_abs)
                             and file_manager.is_binary_file(binary_abs))
                if not is_binary and later_rec and later_rec.existed_before:
                    original_snapshot = self._snapshot_original(later, later_rec)
                    is_binary = bool(original_snapshot and
                                     file_manager.is_binary_file(original_snapshot))
            except Exception:
                is_binary = False
            if is_binary:
                ha = next((f.old_hash for f in earlier.files if f.path == rel), "")
                if later_rec is None:
                    hb = ""
                elif later.status == "partially_rolled_back":
                    hb = later_rec.current_hash if later_rec.current_exists else ""
                elif later.status == "rolled_back":
                    hb = later_rec.old_hash if later_rec.existed_before else ""
                else:
                    hb = later_rec.new_hash
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
            current_rel = ((rec.current_path or rec.new_path or rel) if rec else rel)
            abs_path = file_manager.safe_join(project_root, current_rel)
            current = file_manager.read_text(abs_path)
        except Exception:
            current = None
        versions: list = []
        try:
            if tx.status == "baseline":
                versions.append(("Baseline snapshot (when the workspace was connected)",
                                 self._content_at(project_root, tx, rel, "before")))
            else:
                existed = rec.existed_before if rec else True
                if tx.status == "partially_rolled_back":
                    after_label = "Current transaction state (some changes rolled back)"
                    after_content = self._content_at(project_root, tx, rel, "after")
                elif tx.status == "rolled_back":
                    after_label = "Current transaction state (rolled back)"
                    after_content = self._content_at(project_root, tx, rel, "after")
                else:
                    after_label = "After this transaction"
                    op = rec.operation if rec else ""
                    gone_after = "delete" in op
                    after_content = (None if gone_after else
                                     self._content_at(project_root, tx, rel, "after"))
                versions.append((after_label, after_content))
                versions.append(("Before this transaction",
                                 self._content_at(project_root, tx, rel, "before")
                                 if existed else None))
        except OSError:
            pass
        versions.append(("Current workspace file", current))
        return versions

    def _rebuild_history_text(self, tx: Transaction, rec: FileRecord,
                              rolled_back: set) -> Optional[str]:
        """Recreate a text preview from the immutable original + active changes.

        New transactions no longer need a persistent full-file .current snapshot
        after every per-change undo/redo. The exact current state is deterministic
        from the original snapshot and the recorded declarative changes.
        """
        if not tx.change_records or not tx.backup_dir:
            return None
        try:
            original_src = self._snapshot_original(tx, rec) if rec.existed_before else ""
            original_binary = bool(original_src and file_manager.is_binary_file(original_src))
            active = self._changes_for_rebuild(tx, rec.path, rolled_back)
            if any(change.operation == "delete" for change in active):
                return None
            content_changes = [change for change in active
                               if change.operation not in ("rename", "delete")]

            if original_binary:
                # Binary file-level operations (rename) can be previewed from the
                # original snapshot; text reconstruction is deliberately refused.
                if content_changes:
                    return None
                with open(original_src, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read()

            if original_src:
                original_text = file_manager.read_text(original_src)
                if original_text is None:
                    return None
            else:
                original_text = ""

            if content_changes:
                rebuilt = compute_file_result(
                    original_text, content_changes, self._settings_for_rebuild(tx))
                expected = (rec.current_hash if tx.status == "partially_rolled_back"
                            else rec.new_hash)
                actual = self.backup_mgr._sha256(rebuilt.encode("utf-8"))
                if expected and actual != expected:
                    return None
                return rebuilt

            # With no text operations, return the exact original text view.
            # Do not compare a normalized text read to a raw hash (CRLF/BOM).
            return original_text if rec.existed_before else ""
        except (OSError, HistoryError, ValueError, TypeError):
            return None

    def _content_at(self, project_root: str, tx: Transaction, rel: str,
                    state: str = "after") -> Optional[str]:
        rec = next((f for f in tx.files if f.path == rel or
                    (f.current_path and f.current_path == rel) or
                    (f.new_path and f.new_path == rel)), None)
        snap = tx.backup_dir or ""

        def read(path: str) -> Optional[str]:
            if not path or not os.path.isfile(path):
                return None
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()

        if rec is not None and snap:
            orig_src = (os.path.join(snap, rec.backup_file) if rec.backup_file
                        else self.backup_mgr.snapshot_path(snap, rec.path, ".orig"))
            if state == "before":
                if os.path.isfile(orig_src):
                    return read(orig_src)
                return "" if not rec.existed_before else None

            if tx.status == "rolled_back":
                return read(orig_src) if rec.existed_before and os.path.isfile(orig_src) else None

            if tx.status == "partially_rolled_back":
                if rec.current_exists is False:
                    return None
                # Read snapshots produced by older versions when present. New
                # versions reconstruct this view and avoid storing another full file.
                current_src = (os.path.join(snap, rec.current_backup_file)
                               if rec.current_backup_file else
                               self.backup_mgr.snapshot_path(snap, rec.path, ".current"))
                if os.path.isfile(current_src):
                    return read(current_src)
                return self._rebuild_history_text(
                    tx, rec, set(tx.rolled_back_change_ids))

            if tx.status == "applied":
                if not rec.new_hash:
                    return None
                new_src = self.backup_mgr.snapshot_path(snap, rec.path, ".new")
                if os.path.isfile(new_src):
                    return read(new_src)
                return self._rebuild_history_text(tx, rec, set())

        if rec is None and snap:
            base = self.baseline()
            if base is not None and base.backup_dir:
                cand = self.backup_mgr.snapshot_path(base.backup_dir, rel, ".orig")
                if os.path.isfile(cand):
                    return read(cand)
        try:
            current_rel = ((rec.current_path or rec.new_path or rec.path)
                           if rec is not None else rel)
            return file_manager.read_text(file_manager.safe_join(project_root, current_rel))
        except Exception:
            return None

    # Compatibility helper used by older tests / callers.
    def _set_status(self, tx: Transaction) -> None:
        self.record(tx)

    # -- safe transaction / per-change undo-redo -----------------------------
    def _join_tx(self, project_root: str, tx: Transaction, rel: str) -> str:
        allow_external = bool(tx.apply_settings.get("external_paths", False))
        return file_manager.safe_join(project_root, rel, allow_external=allow_external)

    def _expected_current(self, tx: Transaction, rec: FileRecord):
        if rec.current_exists is not None:
            return (rec.current_path or rec.new_path or rec.path,
                    bool(rec.current_exists), rec.current_hash, rec.current_mode)
        if tx.status == "rolled_back":
            return rec.path, bool(rec.existed_before), rec.old_hash, rec.old_mode
        return rec.new_path or rec.path, bool(rec.new_hash), rec.new_hash, rec.new_mode

    def _assert_path_state(self, abs_path: str, expected_exists: bool,
                           expected_hash: str, label: str,
                           hash_format: str = "raw-sha256",
                           expected_mode: Optional[int] = None) -> None:
        exists = os.path.lexists(abs_path)
        if not expected_exists:
            if exists:
                raise HistoryError(f"{label} should be absent, but a file now exists: {abs_path}")
            return
        if not os.path.isfile(abs_path):
            raise HistoryError(f"{label} is missing or is not a regular file: {abs_path}")
        actual = self.backup_mgr._hash_of_file(abs_path)
        if not expected_hash or not self._matches_record_hash(
                abs_path, expected_hash, hash_format):
            raise HistoryError(
                f"{label} changed since the recorded transaction state. "
                f"Expected SHA-256 {expected_hash[:12] or '(missing)'}, got {actual[:12]}. "
                "No files were modified.")
        if expected_mode is not None:
            actual_mode = stat.S_IMODE(os.stat(abs_path).st_mode)
            if actual_mode != expected_mode:
                raise HistoryError(
                    f"{label} permissions changed since the transaction "
                    f"(expected mode {oct(expected_mode)}, got {oct(actual_mode)}). "
                    "No files were modified.")

    def _snapshot_original(self, tx: Transaction, rec: FileRecord) -> str:
        if not rec.existed_before:
            return ""
        if not rec.old_hash:
            raise HistoryError(f"original hash is missing for {rec.path}; refusing an unverifiable restore")
        if not tx.backup_dir or not os.path.isdir(tx.backup_dir):
            raise HistoryError("backup snapshot missing - cannot safely rebuild this change")
        path = (os.path.join(tx.backup_dir, rec.backup_file) if rec.backup_file
                else self.backup_mgr.snapshot_path(tx.backup_dir, rec.path, ".orig"))
        if not os.path.isfile(path):
            raise HistoryError(f"original backup missing for {rec.path}; single-change rollback is unavailable")
        if rec.old_hash and not self._matches_record_hash(path, rec.old_hash, rec.hash_format):
            raise HistoryError(f"original backup integrity check failed for {rec.path}")
        if rec.old_mode is not None and stat.S_IMODE(os.stat(path).st_mode) != rec.old_mode:
            raise HistoryError(f"original backup permissions failed integrity check for {rec.path}")
        return path

    def _snapshot_new(self, tx: Transaction, rec: FileRecord) -> str:
        if not rec.new_hash:
            return ""
        if not tx.backup_dir or not os.path.isdir(tx.backup_dir):
            raise HistoryError("backup snapshot missing - cannot safely redo this transaction")
        path = self.backup_mgr.snapshot_path(tx.backup_dir, rec.path, ".new")
        if not os.path.isfile(path):
            raise HistoryError(f"post-apply backup missing for {rec.path}; redo is unavailable")
        if not self._matches_record_hash(path, rec.new_hash, rec.hash_format):
            raise HistoryError(f"post-apply backup integrity check failed for {rec.path}")
        if rec.new_mode is not None and stat.S_IMODE(os.stat(path).st_mode) != rec.new_mode:
            raise HistoryError(f"post-apply backup permissions failed integrity check for {rec.path}")
        return path

    def _redo_source(self, tx: Transaction, rec: FileRecord):
        """Return (kind, payload) for a verified transaction Redo source.

        New records may use a byte delta or reuse the original snapshot. Records
        from older versions default to the full .new snapshot path.
        """
        if not rec.new_hash:
            return "delete", None
        strategy = getattr(rec, "redo_strategy", "snapshot") or "snapshot"
        if strategy == "unavailable":
            raise HistoryError(f"redo data is unavailable for {rec.path}; backups were disabled")
        if strategy == "original":
            source = self._snapshot_original(tx, rec)
            if not self._matches_record_hash(source, rec.new_hash, "raw-sha256"):
                raise HistoryError(f"reused original snapshot does not match Redo state for {rec.path}")
            if rec.new_mode is not None and stat.S_IMODE(os.stat(source).st_mode) != rec.new_mode:
                raise HistoryError(f"reused original snapshot permissions do not match Redo state for {rec.path}")
            return "file", source
        if strategy == "delta":
            original = self._snapshot_original(tx, rec)
            try:
                payload = self.backup_mgr.apply_redo_delta(
                    tx.backup_dir, rec.redo_artifact_file, original,
                    rec.old_hash, rec.new_hash)
            except Exception as exc:
                raise HistoryError(f"redo delta verification failed for {rec.path}: {exc}") from exc
            return "bytes", payload
        if strategy == "snapshot":
            return "file", self._snapshot_new(tx, rec)
        raise HistoryError(f"unknown redo strategy '{strategy}' for {rec.path}")

    def _capture_guard(self, project_root: str, tx: Transaction,
                       rel_paths: list, guard_dir: str) -> dict:
        os.makedirs(guard_dir, exist_ok=True)
        states = {}
        for index, rel in enumerate(dict.fromkeys(rel_paths)):
            abs_path = self._join_tx(project_root, tx, rel)
            if os.path.lexists(abs_path) and not os.path.isfile(abs_path):
                raise HistoryError(f"refusing to modify non-file path during rollback: {rel}")
            if os.path.isfile(abs_path):
                backup = os.path.join(guard_dir, f"{index:04d}.before")
                shutil.copy2(abs_path, backup)
                states[rel] = backup
            else:
                states[rel] = None
        return states

    def _verify_guard_snapshot(self, backup: Optional[str], expected_exists: bool,
                               expected_hash: str, expected_mode: Optional[int],
                               hash_format: str, label: str) -> None:
        if not expected_exists:
            if backup is not None:
                raise HistoryError(f"{label} appeared while the rollback guard was being captured")
            return
        if not backup or not os.path.isfile(backup):
            raise HistoryError(f"could not capture a recovery copy of {label}; no changes were made")
        if expected_hash and not self._matches_record_hash(backup, expected_hash, hash_format):
            raise HistoryError(f"{label} changed while its recovery copy was being captured; no changes were made")
        if expected_mode is not None and stat.S_IMODE(os.stat(backup).st_mode) != expected_mode:
            raise HistoryError(f"{label} permissions changed while its recovery copy was being captured; no changes were made")

    def _restore_guard(self, project_root: str, tx: Transaction, states: dict) -> None:
        errors = []
        for rel, backup in states.items():
            try:
                abs_path = self._join_tx(project_root, tx, rel)
                if backup:
                    if os.path.lexists(abs_path) and not os.path.isfile(abs_path):
                        raise OSError("path is not a regular file")
                    self._atomic_copy_file(backup, abs_path)
                else:
                    if os.path.isfile(abs_path):
                        os.remove(abs_path)
                    elif os.path.lexists(abs_path):
                        raise OSError("path is not a regular file")
            except Exception as exc:  # recovery must try every captured path
                errors.append(f"{rel}: {exc}")
        if errors:
            raise HistoryError("CRITICAL: rollback recovery was incomplete: " + "; ".join(errors))

    @staticmethod
    def _atomic_copy_file(source: str, destination: str) -> None:
        """Copy bytes + metadata to a sibling temp file, then atomically replace."""
        directory = os.path.dirname(destination) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".acp-copy-", suffix=".tmp", dir=directory)
        os.close(fd)
        try:
            shutil.copy2(source, temp_path)
            os.replace(temp_path, destination)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @staticmethod
    def _atomic_write_bytes(path: str, payload: bytes, mode: Optional[int] = None) -> None:
        """Write exact bytes via a sibling temporary file and atomic replace."""
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".acp-redo-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            if mode is not None:
                os.chmod(temp_path, mode)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @staticmethod
    def _atomic_write_text(path: str, text: str, mode_source: str = "") -> None:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        mode = None
        source = mode_source if mode_source and os.path.isfile(mode_source) else path
        if os.path.isfile(source):
            mode = stat.S_IMODE(os.stat(source).st_mode)
        temp_path = os.path.join(directory, ".acp-rollback-" + uuid.uuid4().hex + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(temp_path, flags, 0o666)  # respect the platform's umask
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            if mode is not None:
                os.chmod(temp_path, mode)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _settings_for_rebuild(self, tx: Transaction):
        if not tx.apply_settings:
            raise HistoryError("this history entry has no saved matching settings; single-change rollback is unavailable")
        defaults = {"exact_match": True, "hash_validation": True,
                    "context_match": True, "fuzzy_match": False,
                    "fuzzy_threshold": 0.90}
        defaults.update(tx.apply_settings)
        return SimpleNamespace(**defaults)

    def _changes_for_rebuild(self, tx: Transaction, rel: str, rolled_back: set) -> list:
        out = []
        for raw in tx.change_records:
            if raw.get("path") != rel or raw.get("id") in rolled_back:
                continue
            try:
                out.append(Change.from_history_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise HistoryError(f"saved change data is invalid ({raw.get('id', '?')}): {exc}") from exc
        return out

    def _apply_change_state(self, project_root: str, tx: Transaction,
                            rec: FileRecord, change_id: str, action: str) -> Transaction:
        if action not in ("undo", "redo"):
            raise ValueError(action)
        if not tx.change_records:
            raise HistoryError("single-change rollback is unavailable for this older transaction; use transaction rollback")
        if not tx.backup_dir or not os.path.isdir(tx.backup_dir):
            raise HistoryError("backup snapshot missing - cannot safely roll back one change")
        raw_by_id = {str(item.get("id")): item for item in tx.change_records}
        if len(raw_by_id) != len(tx.change_records) or change_id not in raw_by_id:
            raise HistoryError(f"change '{change_id}' is not uniquely recorded in this transaction")
        if raw_by_id[change_id].get("path") != rec.path:
            raise HistoryError(f"change '{change_id}' does not belong to {rec.path}")

        rolled = set(tx.rolled_back_change_ids)
        if action == "undo":
            if change_id in rolled:
                raise HistoryError(f"change {change_id} is already rolled back")
            rolled.add(change_id)
        else:
            if change_id not in rolled:
                raise HistoryError(f"change {change_id} is not rolled back")
            rolled.discard(change_id)

        old_current_path, old_exists, old_hash, old_mode = self._expected_current(tx, rec)
        old_abs = self._join_tx(project_root, tx, old_current_path)
        self._assert_path_state(old_abs, old_exists, old_hash,
                                f"Current state for {rec.path}", rec.hash_format, old_mode)

        active_changes = self._changes_for_rebuild(tx, rec.path, rolled)
        rename_changes = [c for c in active_changes if c.operation == "rename"]
        if len(rename_changes) > 1:
            raise HistoryError(f"cannot safely rebuild {rec.path}: multiple rename operations exist")
        desired_path = rename_changes[0].new_path if rename_changes else rec.path
        if not desired_path:
            raise HistoryError("saved rename operation has no destination path")
        desired_abs = self._join_tx(project_root, tx, desired_path)
        if desired_path != old_current_path and os.path.lexists(desired_abs):
            raise HistoryError(
                f"cannot restore {rec.path}: destination '{desired_path}' already exists; "
                "move that file yourself and retry")

        original_src = self._snapshot_original(tx, rec)
        original_binary = bool(original_src and file_manager.is_binary_file(original_src))
        delete_active = any(c.operation == "delete" for c in active_changes)
        content_changes = [c for c in active_changes if c.operation != "rename"]

        desired_exists = False
        desired_text = None
        copy_source = ""
        if not delete_active:
            if not content_changes:
                desired_exists = rec.existed_before
                copy_source = original_src
            elif original_binary:
                raise HistoryError(
                    f"cannot rebuild binary file {rec.path} with text changes; "
                    "only transaction-level rollback is supported")
            else:
                if rec.existed_before:
                    original_text = file_manager.read_text(original_src)
                    if original_text is None:
                        raise HistoryError(f"could not read original snapshot for {rec.path}")
                else:
                    original_text = ""
                try:
                    desired_text = compute_file_result(
                        original_text, content_changes, self._settings_for_rebuild(tx))
                except Exception as exc:
                    raise HistoryError(
                        f"could not safely rebuild {rec.path} without {change_id}: {exc}") from exc
                desired_exists = True

        paths = [old_current_path]
        if desired_path not in paths:
            paths.append(desired_path)
        guard_dir = tempfile.mkdtemp(prefix=".single-change-guard-", dir=tx.backup_dir)
        guard = {}
        workspace_mutated = False
        old_current_backup = rec.current_backup_file
        try:
            guard = self._capture_guard(project_root, tx, paths, guard_dir)
            self._verify_guard_snapshot(guard.get(old_current_path), old_exists,
                                        old_hash, old_mode, rec.hash_format,
                                        f"current file {old_current_path}")
            # Recheck the live workspace after the guard copy to narrow the
            # race window between initial preflight and the first write.
            self._assert_path_state(old_abs, old_exists, old_hash,
                                    f"Current state for {rec.path}", rec.hash_format, old_mode)
            if desired_path != old_current_path:
                self._verify_guard_snapshot(guard.get(desired_path), False, "", None,
                                            rec.hash_format, f"destination {desired_path}")
                if os.path.lexists(desired_abs):
                    raise HistoryError(f"destination '{desired_path}' appeared during rollback; no files were modified")
            workspace_mutated = True
            if desired_exists:
                if copy_source:
                    if not os.path.isfile(copy_source):
                        raise HistoryError(f"original snapshot unavailable for {rec.path}")
                    self._atomic_copy_file(copy_source, desired_abs)
                else:
                    mode_source = old_abs if os.path.isfile(old_abs) else (copy_source or original_src)
                    if not mode_source:
                        try:
                            mode_source = self._snapshot_new(tx, rec)
                        except HistoryError:
                            mode_source = ""
                    self._atomic_write_text(desired_abs, desired_text or "", mode_source)
                    expected_mode = (rec.new_mode if not rolled and rec.new_mode is not None
                                     else (old_mode if old_exists else
                                           (rec.new_mode if rec.new_mode is not None else rec.old_mode)))
                    if expected_mode is not None:
                        os.chmod(desired_abs, expected_mode)
            elif os.path.isfile(desired_abs):
                os.remove(desired_abs)

            if old_current_path != desired_path and os.path.isfile(old_abs):
                os.remove(old_abs)

            actual_hash = self.backup_mgr._hash_of_file(desired_abs) if desired_exists else ""
            if desired_exists and not actual_hash:
                raise HistoryError(f"could not verify rebuilt file {desired_path}")
            all_ids = {str(item.get("id")) for item in tx.change_records}
            if not rolled and (desired_path != (rec.new_path or rec.path)
                               or desired_exists != bool(rec.new_hash)
                               or (desired_exists and actual_hash != rec.new_hash)
                               or (desired_exists and rec.new_mode is not None
                                   and stat.S_IMODE(os.stat(desired_abs).st_mode) != rec.new_mode)):
                raise HistoryError(
                    f"replaying all recorded changes for {rec.path} did not reproduce "
                    "the original post-apply hash; refusing to mark the change as restored")
            if rolled >= all_ids and (desired_exists != rec.existed_before
                                      or (desired_exists and not self._matches_record_hash(
                                          desired_abs, rec.old_hash, rec.hash_format))
                                      or (desired_exists and rec.old_mode is not None
                                          and stat.S_IMODE(os.stat(desired_abs).st_mode) != rec.old_mode)):
                raise HistoryError(
                    f"rebuilding the original state for {rec.path} failed its integrity check")

            # The temporary guard above is sufficient for operation recovery.
            # History previews reconstruct partial states from .orig + active
            # Change records, so do not persist another full-file snapshot.
            current_backup = ""

            before_hash = old_hash
            rec.current_path = desired_path
            rec.current_hash = actual_hash
            rec.current_exists = desired_exists
            rec.current_mode = (stat.S_IMODE(os.stat(desired_abs).st_mode)
                                if desired_exists else None)
            rec.current_backup_file = current_backup
            tx.rolled_back_change_ids = sorted(rolled)
            if rolled and rolled >= all_ids:
                tx.status = "rolled_back"
            elif rolled:
                tx.status = "partially_rolled_back"
            else:
                tx.status = "applied"
            tx.change_rollback_events.append({
                "change_id": change_id, "action": action, "timestamp": time.time(),
                "path": rec.path, "before_path": old_current_path,
                "after_path": desired_path, "before_hash": before_hash,
                "after_hash": actual_hash,
            })
            self.record(tx)
            if old_current_backup and old_current_backup != current_backup:
                old_snapshot = os.path.join(tx.backup_dir, old_current_backup)
                try:
                    if os.path.isfile(old_snapshot):
                        os.remove(old_snapshot)
                except OSError:
                    pass  # history + workspace are already committed; cleanup is optional
            return tx
        except Exception as exc:
            if guard and workspace_mutated:
                try:
                    self._restore_guard(project_root, tx, guard)
                except Exception as recovery_exc:
                    raise HistoryError(
                        f"{exc}; additionally, restoring the pre-operation state failed: {recovery_exc}") from exc
            if isinstance(exc, HistoryError):
                raise
            raise HistoryError(f"single-change {action} failed: {exc}") from exc
        finally:
            shutil.rmtree(guard_dir, ignore_errors=True)

    def undo_change(self, project_root: str, tx_id: str, change_id: str) -> Transaction:
        tx = self.get(tx_id)
        if tx is None:
            raise HistoryError(f"unknown transaction '{tx_id}'")
        if tx.status not in ("applied", "partially_rolled_back"):
            raise HistoryError(f"cannot undo a change in transaction status '{tx.status}'")
        raw = next((item for item in tx.change_records
                    if str(item.get("id")) == change_id), None)
        if raw is None:
            raise HistoryError("single-change rollback is unavailable for this older transaction; use transaction rollback")
        rec = next((item for item in tx.files if item.path == raw.get("path")), None)
        if rec is None:
            raise HistoryError(f"no file snapshot exists for change {change_id}")
        return self._apply_change_state(project_root, tx, rec, change_id, "undo")

    def redo_change(self, project_root: str, tx_id: str, change_id: str) -> Transaction:
        tx = self.get(tx_id)
        if tx is None:
            raise HistoryError(f"unknown transaction '{tx_id}'")
        if tx.status not in ("partially_rolled_back", "rolled_back"):
            raise HistoryError(f"cannot redo a change in transaction status '{tx.status}'")
        raw = next((item for item in tx.change_records
                    if str(item.get("id")) == change_id), None)
        if raw is None:
            raise HistoryError("single-change redo is unavailable for this older transaction")
        rec = next((item for item in tx.files if item.path == raw.get("path")), None)
        if rec is None:
            raise HistoryError(f"no file snapshot exists for change {change_id}")
        return self._apply_change_state(project_root, tx, rec, change_id, "redo")

    def _preflight_transaction_restore(self, project_root: str, tx: Transaction,
                                       target: str) -> list:
        plans = []
        for rec in tx.files:
            current_path, current_exists, current_hash, current_mode = self._expected_current(tx, rec)
            current_abs = self._join_tx(project_root, tx, current_path)
            self._assert_path_state(current_abs, current_exists, current_hash,
                                    f"Current state for {rec.path}", rec.hash_format, current_mode)
            target_path = rec.path if target == "before" else (rec.new_path or rec.path)
            target_abs = self._join_tx(project_root, tx, target_path)
            if target_path != current_path and os.path.lexists(target_abs):
                raise HistoryError(
                    f"cannot restore {rec.path}: destination '{target_path}' already exists. "
                    "No files were modified.")
            if target == "before":
                source = ("file", self._snapshot_original(tx, rec))
            else:
                source = self._redo_source(tx, rec)
            plans.append((rec, current_path, current_exists, current_hash, current_mode,
                          target_path, target_abs, source))
        return plans

    def undo(self, project_root: str, tx_id: str) -> Transaction:
        tx = self.get(tx_id)
        if tx is None:
            raise HistoryError(f"unknown transaction '{tx_id}'")
        if tx.status == "baseline":
            raise HistoryError("the workspace baseline snapshot cannot be undone")
        if tx.status not in ("applied", "partially_rolled_back"):
            raise HistoryError(f"transaction {tx.id} is already {tx.status}")

        plans = self._preflight_transaction_restore(project_root, tx, "before")
        guard_dir = tempfile.mkdtemp(prefix=".transaction-undo-guard-", dir=tx.backup_dir)
        guard = {}
        workspace_mutated = False
        old_current_backups = [rec.current_backup_file for rec in tx.files
                               if rec.current_backup_file]
        try:
            paths = [p for _r, cur, _e, _h, _m, target, _a, _s in plans for p in (cur, target)]
            guard = self._capture_guard(project_root, tx, paths, guard_dir)
            for rec, current_path, current_exists, current_hash, current_mode, target_path, _target_abs, _source in plans:
                self._verify_guard_snapshot(guard.get(current_path), current_exists,
                                            current_hash, current_mode, rec.hash_format,
                                            f"current file {current_path}")
                self._assert_path_state(self._join_tx(project_root, tx, current_path),
                                        current_exists, current_hash,
                                        f"Current state for {rec.path}", rec.hash_format, current_mode)
                if target_path != current_path:
                    self._verify_guard_snapshot(guard.get(target_path), False, "", None,
                                                rec.hash_format, f"destination {target_path}")
                    if os.path.lexists(self._join_tx(project_root, tx, target_path)):
                        raise HistoryError(f"destination '{target_path}' appeared during undo; no files were modified")
            workspace_mutated = True
            for rec, current_path, _exists, _hash, _mode, target_path, target_abs, source in reversed(plans):
                current_abs = self._join_tx(project_root, tx, current_path)
                if current_path != target_path and os.path.isfile(current_abs):
                    os.remove(current_abs)
                source_kind, source_value = source
                if rec.existed_before:
                    if source_kind != "file":
                        raise HistoryError(f"invalid original restore source for {rec.path}")
                    self._atomic_copy_file(source_value, target_abs)
                elif os.path.isfile(target_abs):
                    os.remove(target_abs)
            for rec in tx.files:
                target_abs = self._join_tx(project_root, tx, rec.path)
                self._assert_path_state(target_abs, rec.existed_before, rec.old_hash,
                                        f"Restored original {rec.path}", rec.hash_format, rec.old_mode)
            all_ids = [str(item.get("id")) for item in tx.change_records]
            if all_ids:
                tx.rolled_back_change_ids = sorted(set(all_ids))
            tx.status = "rolled_back"
            for rec in tx.files:
                rec.current_path = rec.path
                rec.current_hash = rec.old_hash
                rec.current_exists = rec.existed_before
                rec.current_mode = rec.old_mode
                rec.current_backup_file = ""
            self.record(tx)
            for name in old_current_backups:
                stale = os.path.join(tx.backup_dir, name)
                try:
                    if os.path.isfile(stale):
                        os.remove(stale)
                except OSError:
                    pass  # never roll back a committed transaction for cleanup failure
            return tx
        except Exception as exc:
            if guard and workspace_mutated:
                try:
                    self._restore_guard(project_root, tx, guard)
                except Exception as recovery_exc:
                    raise HistoryError(
                        f"transaction undo failed: {exc}; recovery also failed: {recovery_exc}") from exc
            if isinstance(exc, HistoryError):
                raise
            raise HistoryError(f"transaction undo failed: {exc}") from exc
        finally:
            shutil.rmtree(guard_dir, ignore_errors=True)

    def redo(self, project_root: str, tx_id: str) -> Transaction:
        tx = self.get(tx_id)
        if tx is None:
            raise HistoryError(f"unknown transaction '{tx_id}'")
        if tx.status != "rolled_back":
            raise HistoryError(f"transaction {tx.id} is not fully rolled back (status: {tx.status})")

        plans = self._preflight_transaction_restore(project_root, tx, "after")
        guard_dir = tempfile.mkdtemp(prefix=".transaction-redo-guard-", dir=tx.backup_dir)
        guard = {}
        workspace_mutated = False
        old_current_backups = [rec.current_backup_file for rec in tx.files
                               if rec.current_backup_file]
        try:
            paths = [p for _r, cur, _e, _h, _m, target, _a, _s in plans for p in (cur, target)]
            guard = self._capture_guard(project_root, tx, paths, guard_dir)
            for rec, current_path, current_exists, current_hash, current_mode, target_path, _target_abs, _source in plans:
                self._verify_guard_snapshot(guard.get(current_path), current_exists,
                                            current_hash, current_mode, rec.hash_format,
                                            f"current file {current_path}")
                self._assert_path_state(self._join_tx(project_root, tx, current_path),
                                        current_exists, current_hash,
                                        f"Current state for {rec.path}", rec.hash_format, current_mode)
                if target_path != current_path:
                    self._verify_guard_snapshot(guard.get(target_path), False, "", None,
                                                rec.hash_format, f"destination {target_path}")
                    if os.path.lexists(self._join_tx(project_root, tx, target_path)):
                        raise HistoryError(f"destination '{target_path}' appeared during redo; no files were modified")
            workspace_mutated = True
            for rec, current_path, _exists, _hash, _mode, target_path, target_abs, source in plans:
                current_abs = self._join_tx(project_root, tx, current_path)
                source_kind, source_value = source
                if source_kind == "file":
                    self._atomic_copy_file(source_value, target_abs)
                elif source_kind == "bytes":
                    self._atomic_write_bytes(target_abs, source_value, rec.new_mode)
                elif source_kind == "delete":
                    if os.path.isfile(target_abs):
                        os.remove(target_abs)
                else:
                    raise HistoryError(f"invalid Redo source for {rec.path}")
                if current_path != target_path and os.path.isfile(current_abs):
                    os.remove(current_abs)
            for rec in tx.files:
                target_path = rec.new_path or rec.path
                target_abs = self._join_tx(project_root, tx, target_path)
                self._assert_path_state(target_abs, bool(rec.new_hash), rec.new_hash,
                                        f"Re-applied transaction file {target_path}",
                                        rec.hash_format, rec.new_mode)
            tx.status = "applied"
            tx.rolled_back_change_ids = []
            for rec in tx.files:
                rec.current_path = rec.new_path or rec.path
                rec.current_hash = rec.new_hash
                rec.current_exists = bool(rec.new_hash)
                rec.current_mode = rec.new_mode
                rec.current_backup_file = ""
            self.record(tx)
            for name in old_current_backups:
                stale = os.path.join(tx.backup_dir, name)
                try:
                    if os.path.isfile(stale):
                        os.remove(stale)
                except OSError:
                    pass  # never roll back a committed transaction for cleanup failure
            return tx
        except Exception as exc:
            if guard and workspace_mutated:
                try:
                    self._restore_guard(project_root, tx, guard)
                except Exception as recovery_exc:
                    raise HistoryError(
                        f"transaction redo failed: {exc}; recovery also failed: {recovery_exc}") from exc
            if isinstance(exc, HistoryError):
                raise
            raise HistoryError(f"transaction redo failed: {exc}") from exc
        finally:
            shutil.rmtree(guard_dir, ignore_errors=True)


# Compatibility helper retained for the history UI.
def _status_of(self) -> str:  # pragma: no cover - trivial
    return "new" if not self.existed_before else "existing"


FileRecord.status_of = _status_of  # type: ignore[attr-defined]
