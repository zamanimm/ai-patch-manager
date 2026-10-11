"""Response tab: everything about the LLM reply — raw XML, summary, parse.

No redundancy with the Prompt tab: this tab only handles what comes OUT of
the model (paste / clipboard / file import, parse & validate, view).
"""
from __future__ import annotations

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)


class ResponseView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window

        layout = QVBoxLayout(self)
        hint = QLabel("Paste the LLM reply here (or import from clipboard/file), "
                      "then parse & validate it into changes.")
        hint.setStyleSheet("color:#666;")
        layout.addWidget(hint)

        self.txt_response = QPlainTextEdit()
        self.txt_response.setPlaceholderText("<response version=\"1.0\"> ... </response>")
        layout.addWidget(self.txt_response, 1)

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
        self.btn_parse = QPushButton("Parse & Validate → Changes")
        row.addWidget(self.btn_parse)
        layout.addLayout(row)

        self.lbl_info = QLabel("No response imported yet.")
        self.lbl_info.setStyleSheet("color:#888;")
        layout.addWidget(self.lbl_info)

        self.btn_clip.clicked.connect(self._from_clipboard)
        self.btn_file.clicked.connect(self._from_file)
        self.btn_parse.clicked.connect(self._parse)
        self.btn_clear.clicked.connect(self.txt_response.clear)

    # ------------------------------------------------------------------
    def _from_clipboard(self):
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if text.strip():
            self.txt_response.setPlainText(text)
        else:
            QMessageBox.information(self, "Clipboard", "Clipboard is empty.")

    def _from_file(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Import response XML", "",
                                              "XML files (*.xml);;All files (*)")
        if path:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                self.txt_response.setPlainText(fh.read())

    def _parse(self):
        raw = self.txt_response.toPlainText().strip()
        if not raw:
            self.main.error_box("Nothing to import - paste the LLM response first.")
            return
        try:
            n = self.ctrl.import_response(raw)
        except Exception as exc:
            self.lbl_info.setText(f"✗ Rejected: {exc}")
            self.main.error_box(f"Response rejected by parser:\n\n{exc}")
            return
        problems = self.ctrl.validate_current_changes()
        self.reload()
        self.main.open_changes_tab()
        if problems:
            self.main.show_output("Validation problems", "\n".join(problems))

    def reload(self):
        """Show the last imported response + its status."""
        if self.ctrl.response_raw:
            self.txt_response.setPlainText(self.ctrl.response_raw)
            n_ok = sum(1 for c in self.ctrl.changes if c.valid)
            self.lbl_info.setText(
                f"✓ Imported · {len(self.ctrl.changes)} change(s) · "
                f"{n_ok} valid · summary: {self.ctrl.response_summary or '-'}")
        else:
            self.lbl_info.setText("No response imported yet.")
