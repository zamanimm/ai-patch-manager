"""Phase 1 optimizations: non-GUI and optional offscreen GUI regression tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from core import scanner
from core.controller import Controller


class Phase1CoreTests(unittest.TestCase):
    def test_scanner_keeps_excludes_and_results_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('b.py', 'a.py', 'node_modules/dep.py', '.git/x.py'):
                path = Path(tmp) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test')
            self.assertEqual(scanner.scan_project(tmp, ['**/*.py'], []), ['a.py', 'b.py'])

    def test_workspace_config_write_is_valid_json_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Controller()
            c.project.root = tmp
            work = Path(tmp) / '.ai-patch-manager'
            work.mkdir()
            c.selected_files = {'a.py': 'selection'}
            c.save_workspace_config()
            first = json.loads((work / 'config.json').read_text())
            self.assertEqual(first['selected_files']['a.py'], 'selection')
            c.selected_files['a.py'] = 'context'
            c.save_workspace_config()
            last = json.loads((work / 'config.json').read_text())
            self.assertEqual(last['selected_files']['a.py'], 'context')
            self.assertEqual(list(work.glob('.config-*.tmp')), [])


try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from core.qt_binding import load_qt_modules
    load_qt_modules()
    from qtpy.QtWidgets import QApplication, QComboBox, QPlainTextEdit
    from qtpy.QtCore import Qt
    from ui.file_view_dialog import read_preview_text
    from ui.diff_view import DiffView
    from ui.project_view import ModeDelegate
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, 'Requires a supported Qt binding')
class Phase1GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_reads_only_bounded_number_of_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / 'big.txt'
            with big.open('wb') as fh:
                fh.write(b'x' * 2_000_000)
            result = read_preview_text(str(big))
            self.assertIn('preview truncated', result)
            self.assertLess(len(result), 1_000_200)

    def test_diff_keeps_coloured_highlighter_and_plain_text(self):
        widget = DiffView()
        diff = '--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n'
        widget.show_diff(diff)
        self.assertEqual(widget.toPlainText(), diff)
        self.assertIsNotNone(widget.syntax)


if __name__ == '__main__':
    unittest.main()
