"""GUI regression checks. Run after installing requirements with: python -m unittest discover -s tests.

Offscreen Qt tests are safe for CI and do not need an X server.
"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from core.qt_binding import load_qt_modules
    load_qt_modules()
    from qtpy.QtCore import QSettings
    from qtpy.QtGui import QGuiApplication, QTextOption
    from qtpy.QtWidgets import QApplication, QPlainTextEdit
    from ui.file_view_dialog import FileViewDialog
    from ui.layout_utils import flexible_label
    from ui.text_wrap import WrapState, apply_wrap
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "Install a Qt 6 binding to run GUI tests")
class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_wrap_is_on_initially_but_can_be_explicitly_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(os.path.join(tmp, "wrap.ini"), QSettings.IniFormat)
            state = WrapState(settings)
            self.assertTrue(state.enabled())
            state.set_enabled(False)
            self.assertFalse(WrapState(settings).enabled())
            state.set_enabled(True)
            self.assertTrue(WrapState(settings).enabled())

    def test_very_long_unbroken_line_wraps_at_widget_width(self):
        edit = QPlainTextEdit()
        edit.resize(360, 200)
        apply_wrap(edit, True)
        edit.setPlainText("x" * 20_000)
        self.assertEqual(edit.lineWrapMode(), QPlainTextEdit.WidgetWidth)
        self.assertEqual(edit.wordWrapMode(), QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.assertEqual(edit.width(), 360)

    def test_file_view_dialog_stays_bounded_with_long_filename_and_content(self):
        path = "long_filename_" * 800
        dlg = FileViewDialog(path, [("Current file", "some unbroken text " * 2000)])
        dlg.show()
        self.app.processEvents()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.assertLessEqual(dlg.width(), area.width())
        self.assertLessEqual(dlg.height(), area.height())
        self.assertIsInstance(dlg.chk_wrap.isChecked(), bool)
        self.assertEqual(dlg.btn_open_with.text(), "Open with…")
        self.assertEqual(dlg.btn_external.text(), "Open in Default App")
        dlg.close()

if __name__ == "__main__":
    unittest.main()
