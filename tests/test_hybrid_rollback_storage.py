"""Hybrid backup tests: immutable original + compact redo delta when useful."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from core.backup_manager import BackupManager
from core.history_manager import HistoryError, HistoryManager
from core.transaction import TransactionRunner
from models.change import Change, Target
from models.project import ProjectSettings


class HybridRollbackStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.work = self.root / ".ai-patch-manager"
        self.backups = self.work / "backups"
        self.backups.mkdir(parents=True)
        self.history = HistoryManager(str(self.work / "history.json"), str(self.backups))
        self.settings = ProjectSettings()

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, changes):
        report = TransactionRunner(str(self.root), self.settings,
                                   str(self.backups)).apply(changes)
        self.assertTrue(report.ok, report.error)
        self.history.record(report.transaction)
        return report.transaction

    @staticmethod
    def replace(change_id, match, replacement, path="code.py"):
        return Change(id=change_id, path=path, operation="replace",
                      target=Target(match=match), content=replacement, valid=True)

    def test_sparse_text_edits_use_compact_delta_and_redo_exactly(self):
        path = self.root / "code.py"
        middle = b"".join(f"stable_{i} = {i}\n".encode("ascii") for i in range(1200))
        original = b"alpha = 1\n" + middle + b"omega = 2\n"
        path.write_bytes(original)
        tx = self.apply([
            self.replace("chg-a", "alpha = 1", "alpha = 10"),
            self.replace("chg-b", "omega = 2", "omega = 20"),
        ])
        rec = tx.files[0]
        self.assertEqual(rec.redo_strategy, "delta")
        snap = Path(tx.backup_dir)
        self.assertTrue((snap / rec.backup_file).is_file())
        self.assertTrue((snap / rec.redo_artifact_file).is_file())
        self.assertFalse(list(snap.glob("*.new")))
        self.assertLess((snap / rec.redo_artifact_file).stat().st_size,
                        path.stat().st_size * 0.70)

        applied_bytes = path.read_bytes()
        self.history.undo(str(self.root), tx.id)
        self.assertEqual(path.read_bytes(), original)
        self.history.redo(str(self.root), tx.id)
        self.assertEqual(path.read_bytes(), applied_bytes)

    def test_applied_history_preview_reconstructs_without_new_snapshot(self):
        path = self.root / "code.py"
        middle = b"".join(f"line_{i} = {i}\n".encode("ascii") for i in range(600))
        path.write_bytes(b"first = 1\n" + middle)
        tx = self.apply([self.replace("chg-a", "first = 1", "first = 2")])
        rec = tx.files[0]
        self.assertEqual(rec.redo_strategy, "delta")
        self.assertFalse(list(Path(tx.backup_dir).glob("*.new")))
        preview = self.history.file_versions(str(self.root), tx, "code.py")[0][1]
        self.assertEqual(preview, path.read_text(encoding="utf-8"))

    def test_corrupt_delta_blocks_redo_before_workspace_is_modified(self):
        path = self.root / "code.py"
        middle = b"".join(f"unchanged_{i} = {i}\n".encode("ascii") for i in range(500))
        path.write_bytes(b"value = 1\n" + middle)
        tx = self.apply([self.replace("chg-a", "value = 1", "value = 2")])
        rec = tx.files[0]
        self.assertEqual(rec.redo_strategy, "delta")
        self.history.undo(str(self.root), tx.id)
        before = path.read_bytes()
        delta_path = Path(tx.backup_dir) / rec.redo_artifact_file
        delta_path.write_bytes(b'{"version":1,"edits":[broken]}')

        with self.assertRaises(HistoryError):
            self.history.redo(str(self.root), tx.id)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.history.get(tx.id).status, "rolled_back")

    def test_delta_redo_restores_recorded_file_mode(self):
        import os
        if os.name == "nt":
            self.skipTest("POSIX permission bits are not fully represented on Windows")
        path = self.root / "mode.py"
        path.write_text("value = 1\n" + "stable = 2\n" * 100, encoding="utf-8")
        os.chmod(path, 0o750)
        tx = self.apply([self.replace("chg-mode", "value = 1", "value = 3", "mode.py")])
        rec = tx.files[0]
        self.assertEqual(rec.redo_strategy, "delta")
        expected_mode = rec.new_mode
        self.history.undo(str(self.root), tx.id)
        self.history.redo(str(self.root), tx.id)
        self.assertEqual(os.stat(path).st_mode & 0o777, expected_mode)

    def test_redo_artifact_storage_failure_rolls_back_apply(self):
        path = self.root / "code.py"
        original = b"value = 1\n"
        path.write_bytes(original)
        with patch.object(BackupManager, "store_redo_artifact",
                                        side_effect=OSError("simulated backup artifact failure")):
            report = TransactionRunner(str(self.root), self.settings,
                                       str(self.backups)).apply([
                self.replace("chg-a", "value = 1", "value = 2")
            ])
        self.assertFalse(report.ok)
        self.assertTrue(report.rolled_back)
        self.assertEqual(path.read_bytes(), original)

    def test_large_replacement_falls_back_to_full_snapshot(self):
        path = self.root / "code.py"
        path.write_text("x = 1\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-a", "x = 1", "payload = '" + ("z" * 12000) + "'")])
        rec = tx.files[0]
        self.assertEqual(rec.redo_strategy, "snapshot")
        self.assertTrue((Path(tx.backup_dir) / (Path(rec.backup_file).name.replace(".orig", ".new"))).exists()
                        or list(Path(tx.backup_dir).glob("*.new")))
        self.history.undo(str(self.root), tx.id)
        self.history.redo(str(self.root), tx.id)
        self.assertIn("z" * 12000, path.read_text(encoding="utf-8"))

    def test_partial_undo_does_not_persist_another_full_file_and_preview_rebuilds(self):
        path = self.root / "code.py"
        path.write_bytes(b"alpha\nomega\n" + b"stable\n" * 200)
        tx = self.apply([
            self.replace("chg-a", "alpha", "ALPHA"),
            self.replace("chg-b", "omega", "OMEGA"),
        ])
        self.history.undo_change(str(self.root), tx.id, "chg-a")
        saved = self.history.get(tx.id)
        self.assertEqual(saved.files[0].current_backup_file, "")
        self.assertFalse(list(Path(saved.backup_dir).glob("*.current-*")))
        after = self.history.file_versions(str(self.root), saved, "code.py")[0][1]
        self.assertTrue(after.startswith("alpha\nOMEGA\nstable\n"))

    def test_legacy_snapshot_strategy_remains_the_default(self):
        from models.transaction import FileRecord
        record = FileRecord(path="old.py", operation="replace")
        self.assertEqual(record.redo_strategy, "snapshot")
        payload = record.__dict__.copy()
        payload.pop("redo_strategy", None)
        payload.pop("redo_artifact_file", None)
        from models.transaction import Transaction
        tx = Transaction.from_dict({
            "id": "legacy", "timestamp": 1, "files": [payload]
        })
        self.assertEqual(tx.files[0].redo_strategy, "snapshot")


if __name__ == "__main__":
    unittest.main()
