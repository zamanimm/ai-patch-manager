"""Offscreen UI checks for transaction/change selection in History."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from core.qt_binding import load_qt_modules
    load_qt_modules()
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QApplication
except ImportError:  # pragma: no cover - platform dependency
    raise unittest.SkipTest("A supported Qt 6 binding is required for History UI tests")

from core.history_manager import HistoryManager
from core.transaction import TransactionRunner
from models.change import Change, Target
from models.project import ProjectSettings
from ui.history_view import HistoryView


_APP = QApplication.instance() or QApplication([])


class _Main:
    def show_output(self, *_args):
        pass

    def error_box(self, *_args):
        pass

    def _sync_log(self):
        pass


class HistorySingleChangeUiTests(unittest.TestCase):
    def test_change_rows_expose_single_change_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "code.py").write_text("alpha\nomega\n", encoding="utf-8")
            backups = root / ".ai-patch-manager" / "backups"
            history = HistoryManager(str(root / ".ai-patch-manager" / "history.json"),
                                     str(backups))
            changes = [
                Change(id="chg-001", path="code.py", operation="replace",
                       target=Target(match="alpha"), content="ALPHA", valid=True),
                Change(id="chg-002", path="code.py", operation="replace",
                       target=Target(match="omega"), content="OMEGA", valid=True),
            ]
            report = TransactionRunner(str(root), ProjectSettings(), str(backups)).apply(changes)
            self.assertTrue(report.ok, report.error)
            history.record(report.transaction)
            controller = SimpleNamespace(
                history=history, project=SimpleNamespace(root=str(root)),
                list_history=lambda limit=None, offset=0: history.list_transactions(limit, offset),
                baseline_vs_current_diff=lambda: "",
            )
            view = HistoryView(controller, _Main())
            view.reload()
            tx_item = view.tree.topLevelItem(0)
            file_item = next(tx_item.child(i) for i in range(tx_item.childCount())
                             if tx_item.child(i).data(0, Qt.UserRole + 1) == "code.py")
            change_item = file_item.child(0)
            view.tree.setCurrentItem(change_item)
            view._update_actions()
            self.assertEqual(view._current_id(), report.transaction.id)
            self.assertEqual(view._current_change_id(), "chg-001")
            self.assertTrue(view.btn_undo_change.isEnabled())
            self.assertFalse(view.btn_redo_change.isEnabled())
            view.close()

    def test_legacy_change_rows_are_not_undoable_individually(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            backups = root / ".ai-patch-manager" / "backups"
            history = HistoryManager(str(root / ".ai-patch-manager" / "history.json"),
                                     str(backups))
            from models.transaction import FileRecord, Transaction
            tx = Transaction(id="legacy-tx", status="applied",
                              files=[FileRecord(path="legacy.py", operation="replace",
                                                change_ids=["chg-old"])])
            history.record(tx)
            controller = SimpleNamespace(
                history=history, project=SimpleNamespace(root=str(root)),
                list_history=lambda limit=None, offset=0: history.list_transactions(limit, offset),
                baseline_vs_current_diff=lambda: "",
            )
            view = HistoryView(controller, _Main())
            view.reload()
            tx_item = view.tree.topLevelItem(0)
            file_item = next(tx_item.child(i) for i in range(tx_item.childCount())
                             if tx_item.child(i).data(0, Qt.UserRole + 1) == "legacy.py")
            legacy_item = file_item.child(0)
            view.tree.setCurrentItem(legacy_item)
            view._update_actions()
            self.assertFalse(view.btn_undo_change.isEnabled())
            self.assertFalse(view.btn_redo_change.isEnabled())
            view.close()


if __name__ == "__main__":
    unittest.main()
