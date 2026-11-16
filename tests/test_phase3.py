"""Phase 3: recoverable failures, command persistence, search, startup and isolation."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.command_store import CommandStore
from core.diagnostics import record_error


class CommandStorageTests(unittest.TestCase):
    def test_round_trip_and_workspace_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = CommandStore(str(Path(tmp) / "first"))
            two = CommandStore(str(Path(tmp) / "second"))
            self.assertEqual(one.commands(), [])
            self.assertTrue(one.add("pytest -q"))
            self.assertFalse(one.add("pytest -q"))
            one.remember("python run.py")
            self.assertEqual(one.commands(), ["pytest -q"])
            self.assertEqual(one.last_command(), "python run.py")
            self.assertEqual(two.commands(), [])
            self.assertEqual(two.last_command(), "")
            self.assertTrue(one.remove("pytest -q"))
            self.assertFalse(one.remove("pytest -q"))
            self.assertEqual(one.last_command(), "python run.py")

    def test_caps_command_list_and_preserves_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CommandStore(tmp)
            store.write({"custom_key": "keep", "last_command": "echo last"})
            for n in range(24):
                self.assertTrue(store.add(f"cmd-{n}"))
            commands = store.commands()
            self.assertEqual(len(commands), 20)
            self.assertEqual(commands[0], "cmd-23")
            self.assertNotIn("cmd-0", commands)
            self.assertEqual(store.read()["custom_key"], "keep")
            self.assertEqual(store.last_command(), "echo last")

    def test_corrupted_json_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CommandStore(tmp)
            target = Path(store.path)
            target.parent.mkdir(parents=True)
            target.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.add("new")
            self.assertEqual(target.read_text(encoding="utf-8"), "{broken")

    def test_failed_replace_preserves_original_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CommandStore(tmp)
            store.add("original")
            original = Path(store.path).read_bytes()
            with patch("core.command_store.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    store.add("new")
            self.assertEqual(Path(store.path).read_bytes(), original)
            self.assertEqual(list(Path(store.path).parent.glob(".commands-*.tmp")), [])

    def test_legacy_format_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CommandStore(tmp)
            target = Path(store.path)
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"commands": ["build", "test"],
                                          "last_command": "build"}), encoding="utf-8")
            self.assertEqual(store.commands(), ["build", "test"])
            self.assertEqual(store.last_command(), "build")


class DiagnosticsTests(unittest.TestCase):
    def test_error_is_written_with_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "diagnostics.log")
            try:
                raise RuntimeError("example diagnostic failure")
            except RuntimeError as exc:
                self.assertEqual(record_error("Loading test tab", exc, path), path)
            result = Path(path).read_text(encoding="utf-8")
            self.assertIn("Loading test tab", result)
            self.assertIn("RuntimeError: example diagnostic failure", result)

    def test_log_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diagnostics.log"
            path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            record_error("Task", ValueError("error"), str(path))
            self.assertLess(path.stat().st_size, 2 * 1024 * 1024)
            self.assertIn("Earlier diagnostics trimmed", path.read_text(encoding="utf-8"))


class PatchSafetyRegressionTests(unittest.TestCase):
    def test_multi_file_apply_rolls_back_when_later_write_fails(self):
        from core import file_manager
        from core.transaction import TransactionRunner
        from models.change import Change
        from models.project import ProjectSettings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            first = root / "one.txt"
            second = root / "two.txt"
            first.write_bytes(b"first\r\n")
            second.write_bytes(b"second\r\n")
            changes = [
                Change(id="one", path="one.txt", operation="append", content="added", valid=True),
                Change(id="two", path="two.txt", operation="append", content="added", valid=True),
            ]
            original_write = file_manager.write_text

            def simulated_write(path, text):
                if Path(path).name == "two.txt":
                    raise OSError("simulated failure on second file")
                return original_write(path, text)

            with patch("core.transaction.file_manager.write_text", side_effect=simulated_write):
                report = TransactionRunner(str(root), ProjectSettings(),
                                           str(Path(tmp) / "backups")).apply(changes)
            self.assertFalse(report.ok)
            self.assertTrue(report.rolled_back)
            self.assertIn("simulated failure", report.error)
            self.assertEqual(first.read_bytes(), b"first\r\n")
            self.assertEqual(second.read_bytes(), b"second\r\n")


class CommandExecutionIsolationTests(unittest.TestCase):
    def test_controller_uses_captured_working_directory(self):
        from core.controller import Controller
        from core.process_runner import RunResult

        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "original-project")
            ctrl = Controller()
            ctrl.project.root = str(Path(tmp) / "different-project")
            with patch("core.controller.run_command", return_value=RunResult(True, 0, "", "")) as runner:
                ctrl.run("python -V", cwd=root)
            self.assertEqual(runner.call_args.kwargs["cwd"], root)



try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from core.qt_binding import load_qt_modules
    load_qt_modules()
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QApplication, QPlainTextEdit
    from ui.find_bar import FindBar
    from ui.guide_view import GuideView
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "Requires a supported Qt 6 binding")
class Phase3GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_find_debounces_typing_but_navigation_is_immediate(self):
        editor = QPlainTextEdit()
        editor.setPlainText("needle " * 1200)
        bar = FindBar(editor)
        bar.show()
        self.app.processEvents()
        bar.find_edit.setText("needle")
        self.assertEqual(bar.match_count, 0)  # not scanned on keystroke
        self.assertTrue(bar._refresh_timer.isActive())
        bar.find_next()  # navigation must not wait for the debounce
        self.assertEqual(bar.match_count, 1200)
        self.assertFalse(bar._refresh_timer.isActive())
        bar.find_edit.setText("notthere")
        QTest.qWait(240)
        self.assertEqual(bar.match_count, 0)
        bar.close()
        editor.close()

    def test_search_unicode_offsets_and_replace_all(self):
        editor = QPlainTextEdit()
        editor.setPlainText("😀 cat\n😀 cat\n")
        bar = FindBar(editor, allow_replace=True)
        bar.show()
        bar.find_edit.setText("cat")
        bar._refresh()
        self.assertEqual(bar.match_count, 2)
        bar.replace_edit.setText("dog")
        bar.replace_all()
        self.assertEqual(editor.toPlainText(), "😀 dog\n😀 dog\n")
        editor.undo()
        self.assertEqual(editor.toPlainText(), "😀 cat\n😀 cat\n")
        bar.close()
        editor.close()

    def test_guide_is_loaded_without_deferred_state(self):
        guide = GuideView(None, None)
        self.assertIn("Project", guide.browser.toPlainText())
        guide.reload()
        self.assertIn("Project", guide.browser.toPlainText())
        guide.close()


if __name__ == "__main__":
    unittest.main()
