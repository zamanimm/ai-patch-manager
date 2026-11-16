"""Safe transaction-level and per-change undo/redo regression tests."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.history_manager import HistoryError, HistoryManager
from core.transaction import TransactionRunner
from models.change import Change, Target
from models.project import ProjectSettings
from models.transaction import FileRecord, Transaction


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SingleChangeRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.backups = self.root / ".ai-patch-manager" / "backups"
        self.history = HistoryManager(
            str(self.root / ".ai-patch-manager" / "history.json"), str(self.backups))
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
    def replace(cid, match, replacement, path="code.py"):
        return Change(id=cid, path=path, operation="replace",
                      target=Target(match=match), content=replacement, valid=True)

    def test_undo_and_redo_two_changes_independently_in_one_file(self):
        path = self.root / "code.py"
        path.write_bytes(b"alpha\nomega\n")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA"),
                         self.replace("chg-002", "omega", "OMEGA")])
        self.assertEqual(path.read_bytes(), b"ALPHA\nOMEGA\n")

        self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_bytes(), b"alpha\nOMEGA\n")
        self.assertEqual(self.history.get(tx.id).status, "partially_rolled_back")

        self.history.undo_change(str(self.root), tx.id, "chg-002")
        self.assertEqual(path.read_bytes(), b"alpha\nomega\n")
        self.assertEqual(self.history.get(tx.id).status, "rolled_back")

        self.history.redo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_bytes(), b"ALPHA\nomega\n")
        self.history.redo_change(str(self.root), tx.id, "chg-002")
        self.assertEqual(path.read_bytes(), b"ALPHA\nOMEGA\n")
        self.assertEqual(self.history.get(tx.id).status, "applied")

    def test_transaction_undo_after_partial_change_undo_and_full_redo(self):
        path = self.root / "code.py"
        path.write_bytes(b"alpha\nomega\n")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA"),
                         self.replace("chg-002", "omega", "OMEGA")])
        self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_bytes(), b"alpha\nOMEGA\n")
        self.history.undo(str(self.root), tx.id)
        self.assertEqual(path.read_bytes(), b"alpha\nomega\n")
        self.history.redo(str(self.root), tx.id)
        self.assertEqual(path.read_bytes(), b"ALPHA\nOMEGA\n")

    def test_external_edit_during_guard_capture_is_not_overwritten(self):
        path = self.root / "code.py"
        path.write_text("alpha\nomega\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA"),
                         self.replace("chg-002", "omega", "OMEGA")])
        original_capture = self.history._capture_guard

        def capture_then_external_edit(*args, **kwargs):
            states = original_capture(*args, **kwargs)
            path.write_text("external concurrent edit\n", encoding="utf-8")
            return states

        with patch.object(self.history, "_capture_guard", side_effect=capture_then_external_edit):
            with self.assertRaises(HistoryError):
                self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_text(encoding="utf-8"), "external concurrent edit\n")
        self.assertEqual(self.history.get(tx.id).status, "applied")

    def test_manual_edit_after_apply_blocks_single_change_undo_without_touching_file(self):
        path = self.root / "code.py"
        path.write_text("alpha\nomega\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA"),
                         self.replace("chg-002", "omega", "OMEGA")])
        path.write_text("user edit\n", encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(HistoryError):
            self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.history.get(tx.id).status, "applied")

    def test_failed_second_partial_undo_keeps_reconstructed_state(self):
        path = self.root / "code.py"
        path.write_text("alpha\nomega\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA"),
                         self.replace("chg-002", "omega", "OMEGA")])
        self.history.undo_change(str(self.root), tx.id, "chg-001")
        saved = self.history.get(tx.id)
        previous_snapshot = saved.files[0].current_backup_file
        self.assertEqual(previous_snapshot, "")  # new versions reconstruct preview state
        before = path.read_bytes()
        with patch.object(self.history, "record", side_effect=OSError("history disk full")):
            with self.assertRaises(HistoryError):
                self.history.undo_change(str(self.root), tx.id, "chg-002")
        self.assertEqual(path.read_bytes(), before)
        still_saved = self.history.get(tx.id)
        self.assertEqual(still_saved.rolled_back_change_ids, ["chg-001"])
        self.assertEqual(still_saved.files[0].current_backup_file, previous_snapshot)
        self.assertEqual(self.history.file_versions(str(self.root), still_saved, "code.py")[0][1],
                         "alpha\nOMEGA\n")

    def test_legacy_transaction_disables_single_change_undo(self):
        tx = Transaction(id="legacy-tx", timestamp=1.0, status="applied",
                         files=[FileRecord(path="code.py", operation="replace",
                                           change_ids=["chg-001"], new_hash="x")])
        self.history.record(tx)
        with self.assertRaisesRegex(HistoryError, "older transaction"):
            self.history.undo_change(str(self.root), tx.id, "chg-001")

    def test_create_change_can_be_undone_and_redone(self):
        path = self.root / "created.py"
        tx = self.apply([Change(id="chg-create", path="created.py", operation="create",
                                content="print('hello')", valid=True)])
        self.assertTrue(path.exists())
        self.assertEqual(tx.files[0].redo_strategy, "snapshot")
        self.history.undo_change(str(self.root), tx.id, "chg-create")
        self.assertFalse(path.exists())
        self.history.redo_change(str(self.root), tx.id, "chg-create")
        self.assertEqual(path.read_text(encoding="utf-8"), "print('hello')\n")

    def test_delete_change_can_be_undone_and_redone(self):
        path = self.root / "delete-me.txt"
        original = b"preserve me\r\n"
        path.write_bytes(original)
        tx = self.apply([Change(id="chg-delete", path="delete-me.txt",
                                operation="delete", valid=True)])
        self.assertFalse(path.exists())
        self.assertEqual(tx.files[0].redo_strategy, "delete")
        self.history.undo_change(str(self.root), tx.id, "chg-delete")
        self.assertEqual(path.read_bytes(), original)
        self.history.redo_change(str(self.root), tx.id, "chg-delete")
        self.assertFalse(path.exists())

    def test_rename_change_can_be_undone_and_redone(self):
        src = self.root / "old.bin"
        dst = self.root / "new.bin"
        original = b"\x00\x01binary\xff\r\n"
        src.write_bytes(original)
        tx = self.apply([Change(id="chg-rename", path="old.bin", operation="rename",
                                new_path="new.bin", valid=True)])
        self.assertFalse(src.exists())
        self.assertEqual(dst.read_bytes(), original)
        self.assertEqual(tx.files[0].redo_strategy, "original")
        self.history.undo_change(str(self.root), tx.id, "chg-rename")
        self.assertEqual(src.read_bytes(), original)
        self.assertFalse(dst.exists())
        self.history.redo_change(str(self.root), tx.id, "chg-rename")
        self.assertFalse(src.exists())
        self.assertEqual(dst.read_bytes(), original)

    def test_undo_content_change_preserves_rename_and_undo_rename_preserves_content(self):
        src = self.root / "old.py"
        dst = self.root / "new.py"
        src.write_text("value = 1\n", encoding="utf-8")
        tx = self.apply([
            self.replace("chg-replace", "value = 1", "value = 2", "old.py"),
            Change(id="chg-rename", path="old.py", operation="rename",
                   new_path="new.py", valid=True),
        ])
        self.assertEqual(dst.read_text(encoding="utf-8"), "value = 2\n")
        self.history.undo_change(str(self.root), tx.id, "chg-replace")
        self.assertFalse(src.exists())
        self.assertEqual(dst.read_text(encoding="utf-8"), "value = 1\n")
        self.history.redo_change(str(self.root), tx.id, "chg-replace")
        self.assertEqual(dst.read_text(encoding="utf-8"), "value = 2\n")
        self.history.undo_change(str(self.root), tx.id, "chg-rename")
        self.assertTrue(src.exists())
        self.assertFalse(dst.exists())
        self.assertEqual(src.read_text(encoding="utf-8"), "value = 2\n")

    def test_failed_history_write_restores_file_and_does_not_record_undo(self):
        path = self.root / "code.py"
        path.write_text("alpha\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA")])
        before = path.read_bytes()
        with patch.object(self.history, "record", side_effect=OSError("history disk full")):
            with self.assertRaises(HistoryError):
                self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.history.get(tx.id).status, "applied")

    def test_transaction_undo_refuses_manual_modification(self):
        path = self.root / "code.py"
        path.write_text("alpha\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA")])
        path.write_text("manual\n", encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(HistoryError):
            self.history.undo(str(self.root), tx.id)
        self.assertEqual(path.read_bytes(), before)

    def test_partial_state_is_reconstructed_for_history_file_versions(self):
        path = self.root / "code.py"
        path.write_text("alpha\nomega\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA"),
                         self.replace("chg-002", "omega", "OMEGA")])
        self.history.undo_change(str(self.root), tx.id, "chg-001")
        current_tx = self.history.get(tx.id)
        self.assertEqual(current_tx.files[0].current_backup_file, "")
        self.assertFalse(list(Path(current_tx.backup_dir).glob("*.current-*")))
        versions = self.history.file_versions(str(self.root), current_tx, "code.py")
        self.assertEqual(versions[0][1], "alpha\nOMEGA\n")

    def test_single_change_undo_in_one_file_does_not_touch_other_files(self):
        a = self.root / "a.py"
        b = self.root / "b.py"
        a.write_text("alpha\n", encoding="utf-8")
        b.write_text("beta\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-a", "alpha", "ALPHA", "a.py"),
                         self.replace("chg-b", "beta", "BETA", "b.py")])
        self.history.undo_change(str(self.root), tx.id, "chg-a")
        self.assertEqual(a.read_text(encoding="utf-8"), "alpha\n")
        self.assertEqual(b.read_text(encoding="utf-8"), "BETA\n")
        self.history.redo_change(str(self.root), tx.id, "chg-a")
        self.assertEqual(a.read_text(encoding="utf-8"), "ALPHA\n")
        self.assertEqual(b.read_text(encoding="utf-8"), "BETA\n")

    def test_transaction_redo_refuses_manual_change_after_full_undo(self):
        path = self.root / "code.py"
        path.write_text("alpha\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA")])
        self.history.undo(str(self.root), tx.id)
        path.write_text("user content\n", encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(HistoryError):
            self.history.redo(str(self.root), tx.id)
        self.assertEqual(path.read_bytes(), before)

    @unittest.skipIf(__import__("os").name == "nt", "POSIX permission bits are not fully represented on Windows")
    def test_permission_change_after_apply_blocks_rollback(self):
        import os
        path = self.root / "code.py"
        path.write_text("alpha\n", encoding="utf-8")
        os.chmod(path, 0o640)
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA")])
        recorded_mode = self.history.get(tx.id).files[0].current_mode
        changed_mode = 0o600 if recorded_mode != 0o600 else 0o644
        os.chmod(path, changed_mode)
        before = path.read_bytes()
        with self.assertRaisesRegex(HistoryError, "permissions changed"):
            self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_bytes(), before)

    @unittest.skipIf(__import__("os").name == "nt", "POSIX permission bits are not fully represented on Windows")
    def test_single_change_undo_restores_original_file_mode_when_all_changes_removed(self):
        import os
        path = self.root / "code.py"
        path.write_text("alpha\n", encoding="utf-8")
        os.chmod(path, 0o750)
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA")])
        self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_text(encoding="utf-8"), "alpha\n")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o750)

    @unittest.skipIf(__import__("os").name == "nt", "POSIX permission bits are not fully represented on Windows")
    def test_redo_last_single_change_restores_recorded_post_apply_mode(self):
        import os
        path = self.root / "code.py"
        path.write_text("alpha\nomega\n", encoding="utf-8")
        os.chmod(path, 0o750)
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA"),
                         self.replace("chg-002", "omega", "OMEGA")])
        new_mode = self.history.get(tx.id).files[0].new_mode
        self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.history.undo_change(str(self.root), tx.id, "chg-002")
        self.history.redo_change(str(self.root), tx.id, "chg-001")
        self.history.redo_change(str(self.root), tx.id, "chg-002")
        self.assertEqual(os.stat(path).st_mode & 0o777, new_mode)

    def test_apply_failure_rolls_back_even_when_persistent_backups_are_disabled(self):
        from core import file_manager
        from unittest.mock import patch
        a = self.root / "a.py"
        b = self.root / "b.py"
        a.write_bytes(b"alpha\r\n")
        b.write_bytes(b"beta\r\n")
        self.settings.backup_before_apply = False
        original_write = file_manager.write_text
        calls = {"count": 0}

        def fail_second_write(path, content):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated second-file failure")
            return original_write(path, content)

        changes = [self.replace("chg-a", "alpha", "ALPHA", "a.py"),
                   self.replace("chg-b", "beta", "BETA", "b.py")]
        with patch.object(file_manager, "write_text", side_effect=fail_second_write):
            report = TransactionRunner(str(self.root), self.settings,
                                       str(self.backups)).apply(changes)
        self.assertFalse(report.ok)
        self.assertTrue(report.rolled_back)
        self.assertEqual(a.read_bytes(), b"alpha\r\n")
        self.assertEqual(b.read_bytes(), b"beta\r\n")
        self.assertTrue(all(not rec.backup_file for rec in report.transaction.files))

    def test_incomplete_apply_rollback_is_reported_as_critical_and_snapshots_retained(self):
        from core import file_manager
        from unittest.mock import patch
        a = self.root / "a.py"
        b = self.root / "b.py"
        a.write_text("alpha\n", encoding="utf-8")
        b.write_text("beta\n", encoding="utf-8")
        self.settings.backup_before_apply = False
        original_write = file_manager.write_text
        calls = {"count": 0}

        def fail_second_write(path, content):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated write failure")
            return original_write(path, content)

        runner = TransactionRunner(str(self.root), self.settings, str(self.backups))
        changes = [self.replace("chg-a", "alpha", "ALPHA", "a.py"),
                   self.replace("chg-b", "beta", "BETA", "b.py")]
        with patch.object(file_manager, "write_text", side_effect=fail_second_write), \
             patch.object(runner.backup_mgr, "restore", side_effect=OSError("restore failed")):
            report = runner.apply(changes)
        self.assertFalse(report.ok)
        self.assertFalse(report.rolled_back)
        self.assertIn("CRITICAL", report.error)
        self.assertTrue(Path(report.transaction.backup_dir).is_dir())
        self.assertTrue(any(Path(report.transaction.backup_dir).glob("*.orig")))

    def test_backup_disabled_disables_single_change_rollback(self):
        self.settings.backup_before_apply = False
        path = self.root / "code.py"
        path.write_text("alpha\n", encoding="utf-8")
        tx = self.apply([self.replace("chg-001", "alpha", "ALPHA")])
        with self.assertRaises(HistoryError):
            self.history.undo_change(str(self.root), tx.id, "chg-001")
        self.assertEqual(path.read_text(encoding="utf-8"), "ALPHA\n")


if __name__ == "__main__":
    unittest.main()
