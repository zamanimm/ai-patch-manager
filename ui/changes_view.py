"""Changes tab: selective preview + apply.

Fixes vs original:
- _dry_run now uses ctrl._backups_dir() (always fresh) instead of the
  stale main.BACKUPS_DIR that was only set once at MainWindow.__init__.
- reload() preserves the current list selection so the diff panel stays
  visible after a toggle.
- Reject button is always enabled; its action checks validity itself.
- Added "Reject All" convenience button.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from ui.diff_view import DiffView


class ChangesView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window

        layout = QVBoxLayout(self)
        self.lbl_summary = QLabel("No imported response yet.")
        layout.addWidget(self.lbl_summary)

        splitter = QSplitter()
        # Left side: list of changes
        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.addWidget(QLabel("Changes:"))
        self.list = QListWidget()
        list_layout.addWidget(self.list, 1)
        splitter.addWidget(list_container)
        
        # Right side: colored diff view
        diff_container = QWidget()
        diff_layout = QVBoxLayout(diff_container)
        diff_layout.setContentsMargins(0, 0, 0, 0)
        diff_layout.addWidget(QLabel("Diff Preview:"))
        self.diff = DiffView()
        diff_layout.addWidget(self.diff, 1)
        splitter.addWidget(diff_container)
        
        # 40/60 split favoring diff view (more important)
        splitter.setStretchFactor(0, 2)  # Changes list
        splitter.setStretchFactor(1, 3)  # Diff view
        layout.addWidget(splitter, 1)

        btns = QHBoxLayout()
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setToolTip("Tick every valid change (also re-enables rejected ones)")
        self.btn_reject = QPushButton("Reject Selected Change")
        self.btn_reject_all = QPushButton("Reject All")
        self.btn_apply_sel = QPushButton("Apply Selected")
        self.btn_apply_all = QPushButton("Apply All")
        self.btn_dry = QPushButton("Dry Run")
        btns.addWidget(self.btn_select_all)
        btns.addWidget(self.btn_reject)
        btns.addWidget(self.btn_reject_all)
        btns.addStretch(1)
        btns.addWidget(self.btn_dry)
        btns.addWidget(self.btn_apply_all)
        btns.addWidget(self.btn_apply_sel)
        layout.addLayout(btns)

        self.list.itemChanged.connect(self._on_toggle)
        self.list.currentItemChanged.connect(lambda *_: self._show_diff())
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_reject.clicked.connect(self._reject)
        self.btn_reject_all.clicked.connect(self._reject_all)
        self.btn_apply_sel.clicked.connect(lambda: self._apply(all_first=False))
        self.btn_apply_all.clicked.connect(lambda: self._apply(all_first=True))
        self.btn_dry.clicked.connect(self._dry_run)

    # ------------------------------------------------------------------
    def reload(self):
        # Remember the currently selected change id so we can restore it
        current_id = None
        cur = self.list.currentItem()
        if cur:
            current_id = cur.data(Qt.UserRole)

        self.list.blockSignals(True)
        self.list.clear()
        summary = self.ctrl.response_summary or ""
        n_ok = sum(1 for c in self.ctrl.changes if c.valid)
        self.lbl_summary.setText(
            f"Proposed changes: {len(self.ctrl.changes)}  "
            f"(valid: {n_ok})  —  {summary}")

        restore_row = None
        for row, ch in enumerate(self.ctrl.changes):
            label = f"[{'✓' if ch.selected else ' '}] {ch.path}  ·  {ch.operation}  ·  {ch.id}"
            extra = []
            if not ch.valid:
                extra.append(ch.validation_errors[0] if ch.validation_errors else "invalid")
            if ch.match_kind == "fuzzy":
                extra.append("FUZZY MATCH - review carefully!")
            if ch.confidence is not None:
                extra.append(f"confidence {ch.confidence:.2f}")
            if extra:
                label += "   ⚠ " + "; ".join(extra)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, ch.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if ch.selected else Qt.Unchecked)
            if not ch.valid:
                item.setForeground(Qt.gray)
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            self.list.addItem(item)
            if ch.id == current_id:
                restore_row = row

        self.list.blockSignals(False)

        # Restore selection
        if restore_row is not None:
            self.list.setCurrentRow(restore_row)
        elif self.list.count() > 0:
            self.list.setCurrentRow(0)
        self._show_diff()

    def _current(self):
        item = self.list.currentItem()
        cid = item.data(Qt.UserRole) if item else None
        return next((c for c in self.ctrl.changes if c.id == cid), None)

    def _on_toggle(self, item):
        cid = item.data(Qt.UserRole)
        checked = item.checkState() == Qt.Checked
        self.ctrl.set_selected(cid, checked)
        ch = next((c for c in self.ctrl.changes if c.id == cid), None)
        if ch:
            prefix = "[✓]" if ch.selected else "[ ]"
            base = item.text().split("]", 1)[-1]
            item.setText(f"{prefix}{base}")

    def _show_diff(self):
        ch = self._current()
        if ch is None:
            self.diff.show_diff("")
            return
        header = ""
        if ch.reason:
            header += f"# Reason: {ch.reason}\n"
        if ch.summary:
            header += f"# Summary: {ch.summary}\n"
        if ch.match_kind:
            header += f"# Located via: {ch.match_kind}\n"
        body = ch.unified_diff or f"# ({ch.operation}: no line diff for this operation)"
        self.diff.show_diff(header + body)

    def _select_all(self):
        n = self.ctrl.select_all()
        self.reload()
        self.main.statusBar().showMessage(f"Selected all {n} valid change(s).", 4000)

    def _reject(self):
        ch = self._current()
        if ch:
            self.ctrl.reject(ch.id)
            self.reload()

    def _reject_all(self):
        self.ctrl.reject_all()
        self.reload()

    def _dry_run(self):
        from core.transaction import TransactionRunner
        # FIX: use the controller's method so the path is always fresh
        backups_dir = self.ctrl._backups_dir()
        runner = TransactionRunner(self.ctrl.project.root, self.ctrl.project.settings,
                                   backups_dir)
        problems = runner.dry_run(self.ctrl.changes)
        self.main.show_output("Dry run result",
                              "OK — all selected changes can be applied."
                              if not problems else "\n".join(problems))

    def _apply(self, all_first: bool):
        if all_first:
            self.ctrl.apply_all()
            self.reload()
        selected = [c for c in self.ctrl.changes if c.selected and c.valid]
        if not selected:
            self.main.error_box("No valid changes are selected.")
            return
        fuzzy = [c.id for c in selected if c.match_kind == "fuzzy"]
        confirm_msg = (
            f"About to apply {len(selected)} change(s):\n"
            + "\n".join(f"  • {c.id} {c.operation} {c.path}" for c in selected)
            + "\n\nA backup snapshot will be created first. Continue?")
        if fuzzy:
            confirm_msg += ("\n\n⚠ FUZZY matches involved: " + ", ".join(fuzzy)
                            + "\n(These need explicit approval — review the diff carefully.)")
        if self.ctrl.project.settings.require_confirmation:
            resp = QMessageBox.question(self, "Confirm Apply", confirm_msg,
                                        QMessageBox.Yes | QMessageBox.No,
                                        QMessageBox.No)
            if resp != QMessageBox.Yes:
                return
        report = self.ctrl.apply_selected(task=self.main.prompt.txt_task.toPlainText())
        if report.ok:
            QMessageBox.information(
                self, "Applied",
                f"Transaction {report.transaction.id} applied successfully:\n"
                + "\n".join(report.details)
                + "\n\nYou can undo it from the History tab.")
            self.reload()
        else:
            self.main.error_box(f"Apply failed:\n{report.error}")
