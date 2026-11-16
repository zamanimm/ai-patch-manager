"""Regression test for unified-diff background consistency (requires a supported Qt binding)."""
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from core.qt_binding import load_qt_modules
    load_qt_modules()
    from qtpy.QtWidgets import QApplication
    from ui.diff_view import DiffView
    from ui.highlight import HAVE_PYGMENTS
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'A supported Qt binding is required for GUI tests')
class DiffColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @unittest.skipUnless(QT_AVAILABLE and HAVE_PYGMENTS, 'Pygments required')
    def test_syntax_tokens_preserve_added_line_background(self):
        view = DiffView()
        view.show_diff('--- a/example.py\n+++ b/example.py\n@@ -0,0 +1,1 @@\n+import numpy as np\n')
        self.app.processEvents()
        block = view.document().findBlockByNumber(3)
        fmt_ranges = block.layout().formats()
        self.assertTrue(fmt_ranges, 'Expected syntax formats on added line')
        for token_range in fmt_ranges:
            self.assertTrue(token_range.format.background().color().isValid(),
                            'Token format must retain green diff background')
        view.close()

if __name__ == '__main__':
    unittest.main()
