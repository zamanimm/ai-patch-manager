"""Response tab: everything about the LLM reply — raw XML, summary, parse.

No redundancy with the Prompt tab: this tab only handles what comes OUT of
the model (paste / clipboard / file import, parse & validate, view).
"""
from __future__ import annotations

from qtpy.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)
from core.qt_compat import exec_qt
from ui.code_editor import mono_font
from ui.find_bar import wrap_with_find_bar
from ui.highlight import CodeHighlighter
from ui.layout_utils import flexible_label
from ui.text_wrap import apply_wrap


class ResponseView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window

        layout = QVBoxLayout(self)
        hint = QLabel("Paste the LLM reply here (or import from clipboard/file), "
                      "then parse + validate it into changes.")
        hint.setStyleSheet("color:#666;")
        layout.addWidget(hint)

        self.txt_response = QPlainTextEdit()
        self.txt_response.setPlaceholderText("<response version=\"1.0\"> ... </response>")
        self.txt_response.setFont(mono_font(10))
        apply_wrap(self.txt_response, True)
        self.syntax = CodeHighlighter(self.txt_response.document(), language="xml")
        # the one place where the text is meant to be edited: Ctrl+F search, Ctrl+H replace
        layout.addWidget(wrap_with_find_bar(self.txt_response, allow_replace=True), 1)

        row = QHBoxLayout()
        # Import + Clear on the left
        self.btn_clip = QPushButton("Import from Clipboard")
        self.btn_file = QPushButton("Import XML File...")
        self.btn_clear = QPushButton("Clear")
        row.addWidget(self.btn_clip)
        row.addWidget(self.btn_file)
        row.addWidget(self.btn_clear)
        # Main action on the right
        row.addStretch(1)
        self.btn_parse = QPushButton("Parse + Validate → Changes")
        row.addWidget(self.btn_parse)
        layout.addLayout(row)

        self.lbl_info = flexible_label(QLabel("No response imported yet."))
        self.lbl_info.setStyleSheet("color:#888;")
        layout.addWidget(self.lbl_info)

        self._synced_raw = None
        self.txt_response.textChanged.connect(self._on_text_edited)
        self.btn_clip.clicked.connect(self._from_clipboard)
        self.btn_file.clicked.connect(self._from_file)
        self.btn_parse.clicked.connect(self._parse)
        self.btn_clear.clicked.connect(self.txt_response.clear)

    # ------------------------------------------------------------------
    def _from_clipboard(self):
        from qtpy.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if text.strip():
            self.txt_response.setPlainText(text)
        else:
            QMessageBox.information(self, "Clipboard", "Clipboard is empty.")

    def _from_file(self):
        from qtpy.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Import response XML", "",
                                              "XML files (*.xml);;All files (*)")
        if path:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    self.txt_response.setPlainText(fh.read())
            except OSError as exc:
                self.main._report_ui_error("Importing response", exc)
                self.main.show_output("Import failed", str(exc))

    def _parse(self):
        raw = self.txt_response.toPlainText().strip()
        if not raw:
            self.main.error_box("Nothing to import - paste the LLM response first.")
            return
        try:
            self.ctrl.import_response(raw)
        except Exception as exc:
            self.lbl_info.setText(f"✗ Rejected: {exc}")
            self.main.error_box(f"Response rejected by parser:\n\n{exc}")
            return
        problems = self.ctrl.validate_current_changes()
        self.reload()
        self.main.open_changes_tab()
        notice = self.ctrl.response_notice
        if notice and notice.type == "warning":
            self._show_response_warning(notice.text)
        if problems:
            self.main.show_output("Validation problems", "\n".join(problems))

    def _show_response_warning(self, text: str) -> None:
        """Only an explicit new Parse can show this dialog, never tab refresh."""
        from qtpy.QtCore import Qt
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("AI warning — review before applying")
        dialog.setTextFormat(Qt.PlainText)
        dialog.setText(
            "The model flagged something you may need to know. "
            "This has not been independently verified:\n\n" + text)
        dialog.setStandardButtons(QMessageBox.Ok)
        exec_qt(dialog)

    def _on_text_edited(self):
        """Typed / replaced text no longer matches what was parsed."""
        raw = self.ctrl.response_raw
        if raw and self._synced_raw == raw and self.txt_response.toPlainText() != raw:
            self.lbl_info.setText("✎ Edited since the last parse - press Parse + Validate to refresh the Changes tab.")

    def reload(self):
        """Show the last imported response + its status.

        reload() runs on every tab switch, so it must NOT overwrite what the user
        has edited (or replaced): the box is only filled when a new response was
        imported."""
        if self.ctrl.response_raw:
            if self.ctrl.response_raw != self._synced_raw:
                if self.txt_response.toPlainText() != self.ctrl.response_raw:
                    self.txt_response.setPlainText(self.ctrl.response_raw)
                self._synced_raw = self.ctrl.response_raw
            n_ok = sum(1 for c in self.ctrl.changes if c.valid)
            self.lbl_info.setText(
                f"✓ Imported · {len(self.ctrl.changes)} change(s) · "
                f"{n_ok} valid · summary: {self.ctrl.response_summary or '-'}")
        else:
            self._synced_raw = None
            self.lbl_info.setText("No response imported yet.")
