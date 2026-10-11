"""Settings dialog: matching + safety toggles and user rules (sections 44, 71-73)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QGroupBox,
                               QLabel, QPlainTextEdit, QSpinBox, QVBoxLayout)


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.ctrl = controller
        self.setWindowTitle("Settings")
        self.resize(520, 620)
        s = controller.project.settings

        layout = QVBoxLayout(self)

        match_box = QGroupBox("Matching strategy")
        form = QFormLayout(match_box)
        self.chk_exact = QCheckBox("Exact Match"); self.chk_exact.setChecked(s.exact_match)
        self.chk_hash = QCheckBox("Hash Validation"); self.chk_hash.setChecked(s.hash_validation)
        self.chk_context = QCheckBox("Context Match"); self.chk_context.setChecked(s.context_match)
        self.chk_fuzzy = QCheckBox("Fuzzy Match"); self.chk_fuzzy.setChecked(s.fuzzy_match)
        self.spin_fuzzy = QDoubleSpinBox()
        self.spin_fuzzy.setRange(0.5, 1.0); self.spin_fuzzy.setSingleStep(0.01)
        self.spin_fuzzy.setValue(s.fuzzy_threshold)
        form.addRow(self.chk_exact)
        form.addRow(self.chk_hash)
        form.addRow(self.chk_context)
        form.addRow(self.chk_fuzzy)
        form.addRow("Fuzzy Threshold", self.spin_fuzzy)
        layout.addWidget(match_box)

        safe_box = QGroupBox("Safety")
        f2 = QFormLayout(safe_box)
        self.chk_preview = QCheckBox("Require Preview"); self.chk_preview.setChecked(s.require_preview)
        self.chk_confirm = QCheckBox("Require Confirmation"); self.chk_confirm.setChecked(s.require_confirmation)
        self.chk_backup = QCheckBox("Backup Before Apply"); self.chk_backup.setChecked(s.backup_before_apply)
        self.chk_conflict = QCheckBox("Conflict Detection"); self.chk_conflict.setChecked(s.conflict_detection)
        self.chk_external = QCheckBox("Allow External Paths (DANGEROUS)"); self.chk_external.setChecked(s.external_paths)
        self.chk_auto = QCheckBox("Auto Apply (NOT recommended)"); self.chk_auto.setChecked(s.auto_apply)
        for w in (self.chk_preview, self.chk_confirm, self.chk_backup,
                  self.chk_conflict, self.chk_external, self.chk_auto):
            f2.addRow(w)
        layout.addWidget(safe_box)

        prompt_box = QGroupBox("Prompt modes")
        f3 = QFormLayout(prompt_box)
        self.spin_ctx = QSpinBox()
        self.spin_ctx.setRange(0, 50)
        self.spin_ctx.setValue(int(getattr(s, "context_lines", 3) or 3))
        self.spin_ctx.setToolTip(
            "Lines added around each picked range when a file uses the "
            "'context' mode.")
        f3.addRow("Context padding (lines)", self.spin_ctx)
        f3.addRow(QLabel("<span style='color:#666'>Files in <b>selection</b> "
                         "mode send exactly the picked lines; files in "
                         "<b>context</b> mode send them plus this padding. "
                         "Pick ranges via “Select Lines...” on the Project "
                         "tab.</span>"))
        layout.addWidget(prompt_box)

        layout.addWidget(QLabel("User-defined Rules (added to every prompt - section 44):"))
        self.txt_rules = QPlainTextEdit()
        self.txt_rules.setPlaceholderText(
            "- Do not change public APIs.\n- Do not modify test files.\n"
            "- Preserve MATLAB vectorization.")
        self.txt_rules.setPlainText("\n".join(self.ctrl.project.rules))
        self.txt_rules.setMaximumHeight(140)
        layout.addWidget(self.txt_rules)

        note = QLabel("Golden rule: LLM proposes; the application validates; "
                      "the user approves; the application applies.")
        note.setStyleSheet("color:#4a90d9; font-style:italic;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        s = self.ctrl.project.settings
        s.exact_match = self.chk_exact.isChecked()
        s.hash_validation = self.chk_hash.isChecked()
        s.context_match = self.chk_context.isChecked()
        s.fuzzy_match = self.chk_fuzzy.isChecked()
        s.fuzzy_threshold = self.spin_fuzzy.value()
        s.require_preview = self.chk_preview.isChecked()
        s.require_confirmation = self.chk_confirm.isChecked()
        s.backup_before_apply = self.chk_backup.isChecked()
        s.conflict_detection = self.chk_conflict.isChecked()
        s.external_paths = self.chk_external.isChecked()
        s.auto_apply = self.chk_auto.isChecked()
        s.context_lines = int(self.spin_ctx.value())
        self.ctrl.project.rules = [
            line.strip() for line in self.txt_rules.toPlainText().splitlines()
            if line.strip()]
        self.ctrl.save_settings()
        self.accept()
