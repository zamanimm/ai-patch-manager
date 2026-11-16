"""Instructions tab: the pure patch-system instructions for the LLM.

Just the <system> block (role + XML response schema) — nothing else.
Copy it once and reuse it as a custom-instruction / system prompt in any
chat, so you don't have to dig it out of the full generated prompt.
"""
from __future__ import annotations

import time

from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)
from ui.code_editor import mono_font
from ui.find_bar import wrap_with_find_bar
from ui.highlight import CodeHighlighter
from ui.text_wrap import follow_wrap


class InstructionsView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window

        layout = QVBoxLayout(self)
        hint = QLabel("Complete XML patch protocol (the <system> part). "
                      "Copy this into your LLM once as a reusable instruction, "
                      "or enable '<system> instructions' in the Prompt tab to "
                      "include it with each generated prompt.")
        hint.setStyleSheet("color:#666;")
        layout.addWidget(hint)

        # Optional: Tree-Sitter symbol targeting (shared with the Prompt tab)
        self.chk_symbol = QCheckBox("Symbol targeting (Tree-Sitter)")
        self.chk_symbol.setToolTip(
            "Uncheck to remove <symbol> targeting (schema example + rules) from\n"
            "these instructions. Remembered per workspace and shared with the\n"
            "'Symbol targeting' option in the Prompt tab.")
        self.chk_symbol.toggled.connect(self._symbol_toggled)
        layout.addWidget(self.chk_symbol)

        bar = QHBoxLayout()
        self.lbl_info = QLabel("")
        self.btn_copy = QPushButton("Copy Instructions")
        self.btn_save = QPushButton("Save to File...")
        bar.addWidget(self.lbl_info, 1)
        bar.addWidget(self.btn_copy)
        bar.addWidget(self.btn_save)
        layout.addLayout(bar)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        follow_wrap(self.view)
        self.view.setFont(mono_font(10))
        self.syntax = CodeHighlighter(self.view.document(), language="xml")
        layout.addWidget(wrap_with_find_bar(self.view), 1)      # search only: fixed text

        self.btn_copy.clicked.connect(self._copy)
        self.btn_save.clicked.connect(self._save)
        self.reload()

    # ------------------------------------------------------------------
    def _symbol_toggled(self, checked: bool):
        if getattr(self, "_loading", False):
            return
        self.ctrl.project.settings.include_symbol = checked
        self.main.schedule_workspace_save()
        self.reload()
        # keep the Prompt tab's checkbox in sync
        prompt_tab = getattr(self.main, "prompt", None)
        if prompt_tab is not None:
            prompt_tab.reload()

    def reload(self):
        self._loading = True
        self.chk_symbol.setChecked(
            getattr(self.ctrl.project.settings, "include_symbol", False))
        self._loading = False
        text = self.ctrl.instructions_text()
        self.view.setPlainText(text)
        lines = text.count("\n") + 1
        self.lbl_info.setText(f"{len(text)} characters · {lines} lines")

    def _copy(self):
        text = self.view.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Copy", "Nothing to copy.")
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self.lbl_info.setText(f"Copied {len(text)} characters at "
                              f"{time.strftime('%H:%M:%S')}")

    def _save(self):
        text = self.view.toPlainText()
        if not text.strip():
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Save instructions", "",
                                              "XML files (*.xml);;Text files (*.txt);;All files (*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
            except OSError as exc:
                self.main._report_ui_error("Saving instructions", exc)
                self.main.show_output("Save failed", str(exc))
                return
            self.lbl_info.setText(f"Saved to {path}")
