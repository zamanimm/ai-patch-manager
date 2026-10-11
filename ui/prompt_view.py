"""Prompt tab: all prompt-related things in one place.

Fixes vs original:
- reload() now also restores last_task / last_errors from the workspace
  config so they survive an application restart.
- Prompt stats are always shown after generate (not only when _last_built
  is set — the XML path doesn't set _last_built).
- Copy button text is shorter and clearer.
- Keyboard shortcut Ctrl+G triggers Generate XML Prompt.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)


class PromptView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window
        layout = QVBoxLayout(self)

        # Template support removed - XML format is the standard

        layout.addWidget(QLabel("Task (free text):"))
        self.txt_task = QPlainTextEdit()
        self.txt_task.setPlaceholderText(
            "e.g. The controller becomes unstable at low speeds. "
            "Increase the heading-error weight and explain why.")
        self.txt_task.setMaximumHeight(80)
        layout.addWidget(self.txt_task)

        layout.addWidget(QLabel("Errors / output (stdout, stderr, stack trace...):"))
        self.txt_errors = QPlainTextEdit()
        self.txt_errors.setPlaceholderText("Paste compiler / test errors here...")
        self.txt_errors.setMaximumHeight(70)
        layout.addWidget(self.txt_errors)

        # ---- token-saving options (Include: section) ------------------------------------------
        include_label = QLabel("Include in Prompt:")
        include_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(include_label)
        
        opts_row = QHBoxLayout()
        opts_row.setContentsMargins(20, 0, 0, 0)  # Indent checkboxes
        
        self.chk_system = QCheckBox("<system> instructions")
        self.chk_files = QCheckBox("<files> content")
        self.chk_symbol = QCheckBox("Symbol targeting (Tree-Sitter)")
        
        tooltip_text = ("Uncheck to omit this block from the NEXT prompt and save tokens\n"
                       "(e.g. when the LLM already has the context).\n"
                       "The choice is remembered per workspace.")
        for chk in (self.chk_system, self.chk_files, self.chk_symbol):
            chk.setToolTip(tooltip_text)
        
        opts_row.addWidget(self.chk_system)
        opts_row.addWidget(self.chk_files)
        opts_row.addWidget(self.chk_symbol)
        opts_row.addStretch(1)
        layout.addLayout(opts_row)
        
        # Load settings
        self._loading_opts = True
        s = self.ctrl.project.settings
        self.chk_system.setChecked(getattr(s, "include_system", True))
        self.chk_files.setChecked(getattr(s, "include_files", True))
        self.chk_symbol.setChecked(getattr(s, "include_symbol", True))
        self._loading_opts = False
        
        # Connect signals
        self.chk_system.toggled.connect(self._opt_changed)
        self.chk_files.toggled.connect(self._opt_changed)
        self.chk_symbol.toggled.connect(self._opt_changed)

        # ---- preview -------------------------------------------------------
        layout.addWidget(QLabel("Preview — the exact prompt that goes INTO the LLM:"))
        self.prompt_preview = QPlainTextEdit()
        self.prompt_preview.setReadOnly(True)
        self.prompt_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.prompt_preview.setPlaceholderText(
            "Select files in the Project tab, then click Generate XML Prompt...")
        layout.addWidget(self.prompt_preview, 1)

        self.lbl_stats = QLabel("")
        layout.addWidget(self.lbl_stats)

        # ---- action buttons (right-aligned) ------------------------------------------------
        row2 = QHBoxLayout()
        row2.addStretch(1)  # Push buttons to the right
        self.btn_generate = QPushButton("Generate XML Prompt  [Ctrl+G]")
        self.btn_copy = QPushButton("Copy → Clipboard")
        self.btn_save = QPushButton("Save...")
        row2.addWidget(self.btn_generate)
        row2.addWidget(self.btn_copy)
        row2.addWidget(self.btn_save)
        layout.addLayout(row2)

        self.btn_generate.clicked.connect(self._generate_xml)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_save.clicked.connect(self._save)

        # Keyboard shortcut
        sc = QShortcut(QKeySequence("Ctrl+G"), self)
        sc.activated.connect(self._generate_xml)

    # ------------------------------------------------------------------
    def _opt_changed(self, _checked: bool):
        if getattr(self, "_loading_opts", False):
            return
        s = self.ctrl.project.settings
        s.include_system = self.chk_system.isChecked()
        s.include_files = self.chk_files.isChecked()
        s.include_symbol = self.chk_symbol.isChecked()
        try:
            self.ctrl.save_workspace_config()
        except Exception:
            pass
        # keep the Instructions tab (checkbox + text) in sync
        patch_tab = getattr(self.main, "patch_tab", None)
        if patch_tab is not None:
            patch_tab.reload()
        hint = []
        if not s.include_system:
            hint.append("<system> omitted")
        if not s.include_files:
            hint.append("<files> omitted")
        msg = ("Next prompt will " + " and ".join(hint)) if hint \
            else "Next prompt will include all blocks"
        self.main.statusBar().showMessage(msg + " (saved in workspace config).", 5000)

    def _show(self, text: str):
        self.prompt_preview.setPlainText(text)
        lines = text.count("\n") + 1
        self.lbl_stats.setText(
            f"Selected files: {len(self.ctrl.selected_files)}   "
            f"Prompt: {lines} lines · ~{int(len(text)/4)} tokens")

    def _generate_xml(self):
        try:
            text = self.ctrl.build_full_xml_prompt(self.txt_task.toPlainText(),
                                                   self.txt_errors.toPlainText())
        except Exception as exc:
            self.main.error_box(str(exc))
            return
        self._show(text)
        self.main.patch_tab.reload()

    def _copy(self):
        if not self.ctrl.prompt_text:
            self.main.error_box("Generate a prompt first.")
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.ctrl.prompt_text)
        self.ctrl.mark_sent()
        self.main.patch_tab.reload()
        self.main.statusBar().showMessage(
            "Prompt copied — paste it into your LLM, then use the Response tab.",
            8000)

    def _save(self):
        if not self.ctrl.prompt_text:
            self.main.error_box("Generate a prompt first.")
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Save prompt", "",
                                              "XML files (*.xml);;Text files (*.txt);;All files (*)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.ctrl.prompt_text)
            self.lbl_stats.setText(f"Saved prompt to {path}")

    def reload(self):
        """Reflect the current generated prompt (if any) + saved options.

        FIX: also restore last_task / last_errors from the workspace config
        so they survive an application restart.
        """
        self._loading_opts = True
        s = self.ctrl.project.settings
        self.chk_system.setChecked(getattr(s, "include_system", True))
        self.chk_files.setChecked(getattr(s, "include_files", True))
        self.chk_symbol.setChecked(getattr(s, "include_symbol", True))
        self._loading_opts = False

        if self.ctrl.prompt_text:
            self.prompt_preview.setPlainText(self.ctrl.prompt_text)

        # Restore saved task / errors text if the fields are still empty
        if not self.txt_task.toPlainText().strip():
            saved = getattr(self.ctrl, "last_task", "") or ""
            if saved:
                self.txt_task.setPlainText(saved)
        if not self.txt_errors.toPlainText().strip():
            saved = getattr(self.ctrl, "last_errors", "") or ""
            if saved:
                self.txt_errors.setPlainText(saved)
