"""Regression coverage for restoring Project-tab selections on app startup."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.controller import Controller


class WorkspaceSelectionPersistenceTests(unittest.TestCase):
    def test_selected_files_round_trip_through_workspace_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai-patch-manager").mkdir()
            first = Controller()
            first.project.root = str(root)
            first.selected_files = {
                "src/config.py": "full",
                "tests/test_core.py": "selection",
            }
            first.save_workspace_config()

            config = json.loads((root / ".ai-patch-manager" / "config.json").read_text(
                encoding="utf-8"))
            self.assertEqual(config["selected_files"], first.selected_files)

            second = Controller()
            second._attach_workspace(str(root), reload_config=True)
            self.assertEqual(second.selected_files, first.selected_files)

    def test_project_view_startup_path_triggers_scan_before_render(self):
        """GUI regression: restored root + empty files must initiate a scan."""
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QApplication
            from ui.project_view import ProjectView
        except ImportError:
            self.skipTest("Requires PySide6")

        app = QApplication.instance() or QApplication([])

        class StatusBar:
            def showMessage(self, *_args, **_kwargs):
                pass

        class MainStub:
            def __init__(self):
                self._status = StatusBar()

            def statusBar(self): return self._status
            def populate_recent_menu(self, *_args): pass
            def open_settings(self): pass
            def update_prompt_stats(self): pass
            def error_box(self, *_args): pass
            def _report_ui_error(self, *_args): pass
            def show_output(self, *_args): pass
            def on_project_opened(self): pass

        class ProjectStub:
            def __init__(self, root):
                self.root = str(root)
                self.include = []
                self.exclude = []

            def is_open(self): return True

        class ControllerStub:
            def __init__(self, root):
                self.project = ProjectStub(root)
                self.files = []  # Controller startup restores root but not scan results.
                self.selected_files = {"src/keep.py": "full"}
                self.file_selections = {}
                self.logs = []

            def log(self, message): self.logs.append(message)
            def get_file_ranges(self, _rel): return []
            def save_workspace_config(self): pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "keep.py").write_text("print('keep')\n", encoding="utf-8")
            ctrl = ControllerStub(root)
            view = ProjectView(ctrl, MainStub())
            try:
                # Let the background scanner deliver its queued Qt signal.
                for _ in range(100):
                    app.processEvents()
                    if view._tree_ready:
                        break
                    from PySide6.QtTest import QTest
                    QTest.qWait(20)
                self.assertTrue(view._tree_ready, "startup did not finish the initial scan")
                found = None
                stack = [view.tree.topLevelItem(i)
                         for i in range(view.tree.topLevelItemCount())]
                while stack:
                    item = stack.pop()
                    if item.data(1, Qt.UserRole) == "src/keep.py":
                        found = item
                        break
                    stack.extend(item.child(i) for i in range(item.childCount()))
                self.assertIsNotNone(found, "restored project's file is missing from tree")
                self.assertEqual(found.checkState(0), 2)  # Qt.Checked
                view.flush_pending_save()
                self.assertEqual(ctrl.selected_files.get("src/keep.py"), "full")
            finally:
                view.close()
                view.deleteLater()
                app.processEvents()

    def test_unscanned_but_existing_selection_is_not_discarded(self):
        """Hidden/excluded/large files remain selected if still present on disk."""
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QApplication
            from ui.project_view import ProjectView
        except ImportError:
            self.skipTest("Requires PySide6")

        app = QApplication.instance() or QApplication([])

        class StatusBar:
            def showMessage(self, *_args, **_kwargs): pass

        class MainStub:
            def statusBar(self): return StatusBar()
            def populate_recent_menu(self, *_args): pass
            def open_settings(self): pass
            def update_prompt_stats(self): pass
            def error_box(self, *_args): pass
            def _report_ui_error(self, *_args): pass
            def show_output(self, *_args): pass
            def on_project_opened(self): pass

        class ProjectStub:
            def __init__(self, root):
                self.root = str(root)
                self.include = []
                self.exclude = []
            def is_open(self): return True

        class ControllerStub:
            def __init__(self, root):
                self.project = ProjectStub(root)
                self.files = ["visible.py"]
                self.selected_files = {".private/keep.py": "context"}
            def log(self, _message): pass
            def get_file_ranges(self, _rel): return []
            def save_workspace_config(self): pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".private").mkdir()
            (root / ".private" / "keep.py").write_text("pass\n", encoding="utf-8")
            (root / "visible.py").write_text("pass\n", encoding="utf-8")
            ctrl = ControllerStub(root)
            view = ProjectView(ctrl, MainStub())
            try:
                from PySide6.QtTest import QTest
                for _ in range(100):
                    app.processEvents()
                    if view._tree_ready:
                        break
                    QTest.qWait(20)
                self.assertTrue(view._tree_ready, "startup scan did not complete")
                view._sync_selection(save=False)
                self.assertEqual(ctrl.selected_files.get(".private/keep.py"), "context")
            finally:
                view.close()
                view.deleteLater()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
