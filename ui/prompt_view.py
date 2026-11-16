"""Prompt tab: all prompt-related things in one place.

Fixes vs original:
- reload() now also restores last_task / last_errors from the workspace
  config so they survive an application restart.
- Prompt stats are always shown after generate.
- Copy button text is shorter and clearer.
- Keyboard shortcut Ctrl+G triggers Generate XML Prompt.
"""
from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtGui import QKeySequence, QShortcut
from qtpy.QtWidgets import (QCheckBox, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)
from ui.code_editor import mono_font
from ui.find_bar import wrap_with_find_bar
from ui.highlight import CodeHighlighter
from ui.text_wrap import apply_wrap, follow_wrap
from ui.layout_utils import flexible_label
from core.optional_dependencies import tree_sitter_status, TREE_SITTER_INSTALL_COMMAND


class PromptView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Task (free text):"))
        self.txt_task = QPlainTextEdit()
        self.txt_task.setPlaceholderText(
            "e.g. The controller becomes unstable at low speeds. "
            "Increase the heading-error weight and explain why.")
        self.txt_task.setMaximumHeight(80)
        apply_wrap(self.txt_task, True)
        layout.addWidget(self.txt_task)

        layout.addWidget(QLabel("Errors / output (stdout, stderr, stack trace...):"))
        self.txt_errors = QPlainTextEdit()
        self.txt_errors.setPlaceholderText("Paste compiler / test errors here...")
        self.txt_errors.setMaximumHeight(70)
        apply_wrap(self.txt_errors, True)
        layout.addWidget(self.txt_errors)

        # ---- token-saving options (Include: section) ------------------------------------------
        include_label = QLabel("Include in Prompt:")
        include_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(include_label)
        
        opts_row = QVBoxLayout()  # Two rows prevent the options from widening a small window.
        opts_row.setContentsMargins(20, 0, 0, 0)
        
        self.chk_system = QCheckBox("<system> instructions")
        self.chk_user_rules = QCheckBox("<user_rules>")
        self.btn_edit_rules = QPushButton("Edit rules…")
        self.btn_edit_rules.setFlat(True)
        self.btn_edit_rules.setCursor(Qt.PointingHandCursor)
        self.btn_edit_rules.setStyleSheet("color: #1c7ed6; text-decoration: underline;")
        self.chk_errors = QCheckBox("<errors>")
        self.chk_files = QCheckBox("<files> content")
        self.chk_symbol = QCheckBox("Symbol targeting (Tree-Sitter)")
        self.lbl_symbol_status = flexible_label(QLabel(""))
        self.lbl_symbol_status.setStyleSheet("color: #b45309; margin-left: 20px;")
        self.lbl_symbol_status.setVisible(False)
        self.chk_binary = QCheckBox("Binary files (base64)")
        self.chk_project_tree = QCheckBox("Project tree (paths only)")

        tooltip_text = ("Uncheck to omit this block from the NEXT prompt and save tokens\n"
                       "(e.g. when the LLM already has the context).\n"
                       "The choice is remembered per workspace.")
        for chk in (self.chk_system, self.chk_files):
            chk.setToolTip(tooltip_text)
        self.chk_user_rules.setToolTip(
            "Your own rules for the LLM, one per line.\n"
            "Set them with 'Edit rules…' (or Project tab ▸ Rules…).\n"
            "If no rules are set, the <user_rules> block is left out automatically.")
        self.btn_edit_rules.setToolTip("Open Settings to write your rules (one per line)")
        self.chk_errors.setToolTip(
            "Send the 'Errors / output' box above as <errors>.\n"
            "Off by default. Ticked automatically by 'Send output → Errors box' in the Run tab.\n"
            "An empty box is never sent.")
        self.chk_symbol.setToolTip(
            "Add <symbol_targeting> and the <symbol> instructions (Tree-Sitter)\n"
            "so the LLM can target functions / classes by name. Off by default.")
        self._tree_sitter_status = tree_sitter_status()
        self._configure_symbol_option()
        self.chk_binary.setToolTip(
            "Include binary file contents as base64 in the prompt. Off by default;\n"
            "binary files are otherwise represented by their size and SHA-256 hash.")
        self.chk_project_tree.setToolTip(
            "Include the scanned project folder/file structure as <project_tree>.\n"
            "Only names and paths are included—never file contents. Off by default.\n"
            "Respects the project's include/exclude patterns.")

        top_options = QHBoxLayout()
        for w in (self.chk_system, self.chk_user_rules, self.btn_edit_rules):
            top_options.addWidget(w)
        top_options.addStretch(1)
        bottom_options = QHBoxLayout()
        for w in (self.chk_errors, self.chk_files, self.chk_symbol):
            bottom_options.addWidget(w)
        bottom_options.addStretch(1)
        binary_options = QHBoxLayout()
        binary_options.addWidget(self.chk_binary)
        binary_options.addStretch(1)
        tree_options = QHBoxLayout()
        tree_options.addWidget(self.chk_project_tree)
        tree_options.addStretch(1)
        opts_row.addLayout(top_options)
        opts_row.addLayout(bottom_options)
        opts_row.addLayout(binary_options)
        opts_row.addLayout(tree_options)
        layout.addLayout(opts_row)
        layout.addWidget(self.lbl_symbol_status)

        self.lbl_opt_warn = flexible_label(QLabel(""))
        self.lbl_opt_warn.setStyleSheet("color: #b45309; margin-left: 20px;")
        self.lbl_opt_warn.setVisible(False)
        layout.addWidget(self.lbl_opt_warn)

        # Load settings
        self._load_opts()

        # Connect signals
        for chk in (self.chk_system, self.chk_user_rules, self.chk_errors,
                    self.chk_files, self.chk_symbol, self.chk_binary,
                    self.chk_project_tree):
            chk.toggled.connect(self._opt_changed)
        self.btn_edit_rules.clicked.connect(lambda: self.main.open_settings())
        self.txt_errors.textChanged.connect(self._refresh_hints)

        # ---- preview -------------------------------------------------------
        layout.addWidget(QLabel("Preview — the exact prompt that goes INTO the LLM:"))
        self.prompt_preview = QPlainTextEdit()
        self.prompt_preview.setReadOnly(True)
        follow_wrap(self.prompt_preview)
        self.prompt_preview.setFont(mono_font(10))
        self.prompt_preview.setPlaceholderText(
            "Select files in the Project tab, then click Generate XML Prompt...")
        self.preview_syntax = CodeHighlighter(self.prompt_preview.document(), language="xml")
        # search only: the preview is exactly what Copy sends (replace would not change it)
        layout.addWidget(wrap_with_find_bar(self.prompt_preview), 1)

        self.lbl_stats = flexible_label(QLabel(""))
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
    def _configure_symbol_option(self):
        """Disable Symbol targeting when the optional core/grammars are absent."""
        status = self._tree_sitter_status
        if not status.available:
            self.chk_symbol.setEnabled(False)
            self.chk_symbol.setText("Symbol targeting (unavailable)")
            message = (
                "Symbol targeting is optional and is currently unavailable.\n"
                f"{status.detail}\n\nInstall with:\n{TREE_SITTER_INSTALL_COMMAND}\n"
                "Then restart AI Patch Manager.")
            self.chk_symbol.setToolTip(message)
            self.lbl_symbol_status.setText(
                "Symbol targeting is disabled. Install the optional Tree-Sitter packages "
                f"with: {TREE_SITTER_INSTALL_COMMAND}")
            self.lbl_symbol_status.setVisible(True)
            return

        self.chk_symbol.setEnabled(True)
        self.chk_symbol.setText("Symbol targeting (Tree-Sitter)")
        languages = ", ".join(status.languages)
        self.chk_symbol.setToolTip(
            "Add <symbol_targeting> and <symbol> instructions.\n"
            f"Working grammars in this environment: {languages}.\n"
            "Other languages will use exact match / regex / line targeting.")
        self.lbl_symbol_status.setText(
            "Available Tree-Sitter grammars: " + languages)
        self.lbl_symbol_status.setVisible(True)

    # ------------------------------------------------------------------
    def _load_opts(self):
        """Checkboxes <- saved workspace settings (without triggering a save)."""
        self._loading_opts = True
        s = self.ctrl.project.settings
        self.chk_system.setChecked(getattr(s, "include_system", False))
        self.chk_user_rules.setChecked(getattr(s, "include_user_rules", True))
        self.chk_errors.setChecked(getattr(s, "include_errors", False))
        self.chk_files.setChecked(getattr(s, "include_files", True))
        self.chk_symbol.setChecked(getattr(s, "include_symbol", False))
        self.chk_binary.setChecked(getattr(s, "include_binary_files", False))
        self.chk_project_tree.setChecked(getattr(s, "include_project_tree", False))
        self._loading_opts = False
        self._refresh_hints()

    def _refresh_hints(self):
        """Rule count on the checkbox + a warning when typed errors would NOT be sent."""
        n = len([r for r in (self.ctrl.project.rules or []) if r and r.strip()])
        self.chk_user_rules.setText(f"<user_rules> ({n})" if n else "<user_rules> (none set)")
        errors_typed = bool(self.txt_errors.toPlainText().strip())
        warn = errors_typed and not self.chk_errors.isChecked()
        self.lbl_opt_warn.setText(
            "⚠ The Errors box has text, but <errors> is unchecked - it will NOT be sent."
            if warn else "")
        self.lbl_opt_warn.setVisible(warn)

    def _opt_changed(self, _checked: bool):
        if getattr(self, "_loading_opts", False):
            return
        s = self.ctrl.project.settings
        s.include_system = self.chk_system.isChecked()
        s.include_user_rules = self.chk_user_rules.isChecked()
        s.include_errors = self.chk_errors.isChecked()
        s.include_files = self.chk_files.isChecked()
        s.include_symbol = self.chk_symbol.isChecked()
        s.include_binary_files = self.chk_binary.isChecked()
        s.include_project_tree = self.chk_project_tree.isChecked()
        self._refresh_hints()
        self.main.schedule_workspace_save()
        # keep the Instructions tab (checkbox + text) in sync
        patch_tab = getattr(self.main, "patch_tab", None)
        if patch_tab is not None:
            patch_tab.reload()
        hint = []
        if not s.include_system:
            hint.append("<system> omitted - your LLM must already have the Instructions (Instructions tab ▸ Copy Instructions)")
        if not s.include_user_rules:
            hint.append("<user_rules> omitted")
        if not s.include_errors:
            hint.append("<errors> omitted")
        if not s.include_files:
            hint.append("<files> omitted")
        if s.include_binary_files:
            hint.append("binary files included as base64")
        if s.include_project_tree:
            hint.append("project tree paths included (no file contents)")
        msg = ("Next prompt will " + " and ".join(hint)) if hint \
            else "Next prompt will include all blocks"
        self.main.statusBar().showMessage(msg + " (saved in workspace config).", 5000)

    def _show(self, text: str):
        self.prompt_preview.setPlainText(text)
        lines = text.count("\n") + 1
        note = ""
        if not getattr(self.ctrl.project.settings, "include_system", False):
            note = ("   ·   ⓘ <system> is not included: paste the Instructions into your LLM once "
                    "(Instructions tab ▸ Copy Instructions) or tick <system>")
        characters = len(text)
        omitted = text.count("[binary file omitted:")
        binary_note = f" · {omitted} binary file(s) represented by hash only" if omitted else ""
        warning = (f" · ⚠ Large prompt: check the context limit of your target model"
                   if characters >= 320_000 else "")
        self.lbl_stats.setText(
            f"Selected files: {len(self.ctrl.selected_files)}   "
            f"Prompt: {lines:,} lines · {characters:,} characters"
            f"{binary_note}{warning}{note}")

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
        from qtpy.QtWidgets import QApplication
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
        from qtpy.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Save prompt", "",
                                              "XML files (*.xml);;Text files (*.txt);;All files (*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self.ctrl.prompt_text)
            except OSError as exc:
                self.main._report_ui_error("Saving prompt", exc)
                self.main.show_output("Save failed", str(exc))
                return
            self.lbl_stats.setText(f"Saved prompt to {path}")

    def reload(self):
        """Reflect the current generated prompt (if any) + saved options.

        FIX: also restore last_task / last_errors from the workspace config
        so they survive an application restart.
        """
        self._load_opts()

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
