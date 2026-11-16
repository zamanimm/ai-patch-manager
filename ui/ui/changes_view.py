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

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from core.qt_compat import exec_qt
from ui.diff_view import DiffView
from ui.code_editor import CodeEditor
from ui.highlight import CodeHighlighter
from ui.external_editor import open_in_code_editor
from ui.layout_utils import flexible_label
from core import file_manager
import os
from ui.find_bar import sub_nonempty, wrap_with_find_bar



class _PatchReplacer:
    """Search & replace inside the PROPOSED code of the parsed patches.

    Only what would be written is edited (a change's replacement). The original
    file lines shown in the diff and the target anchors are never changed.
    One level of undo."""

    scopes = [("selected", "Selected change"), ("all", "All changes")]

    def __init__(self, view):
        self.view = view
        self._undo = None            # {change id: (old content, old 'edited' flag)}

    def _targets(self, scope: str):
        ctrl = self.view.ctrl
        if scope == "all":
            return list(ctrl.changes)
        cur = self.view._current()
        return [cur] if cur is not None else []

    def replace(self, pattern, repl: str, literal: bool, scope: str, dry_run: bool):
        """-> (occurrences replaced, changes affected). dry_run only counts."""
        fn = (lambda m: repl) if literal else (lambda m: m.expand(repl))
        edits, total = {}, 0
        for ch in self._targets(scope):
            if ch.content is None:
                continue
            new, n = sub_nonempty(pattern, fn, ch.content)
            if n and new != ch.content:
                edits[ch.id] = (ch, new)
                total += n
        if dry_run or not edits:
            return total, len(edits)
        if self.view._has_unsaved_draft() and not self.view._commit_pending_edit():
            return 0, 0
        self._undo = {cid: (ch.content, ch.edited) for cid, (ch, _n) in edits.items()}
        for ch, new in edits.values():
            self.view.ctrl.update_change_content(ch, new)
        self.view.reload()
        return total, len(edits)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        by_id = {c.id: c for c in self.view.ctrl.changes}
        for cid, (old, was_edited) in self._undo.items():
            if cid in by_id:
                self.view.ctrl.update_change_content(by_id[cid], old, mark_edited=was_edited)
        self._undo = None
        self.view.reload()
        return True


class ChangesView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window
        self._edit_change_id = None
        self._edit_change_object = None
        self._edit_initial_content = None
        self._in_reload = False

        layout = QVBoxLayout(self)
        self.lbl_summary = flexible_label(QLabel("No imported response yet."))
        layout.addWidget(self.lbl_summary)
        # Optional AI-provided note. Use plain text: the response is untrusted.
        self.lbl_note = flexible_label(QLabel(""))
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setTextFormat(Qt.PlainText)
        self.lbl_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_note.setMaximumHeight(self.lbl_note.fontMetrics().lineSpacing() * 7 + 24)
        self.lbl_note.setVisible(False)
        layout.addWidget(self.lbl_note)

        self.splitter = QSplitter()
        splitter = self.splitter
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
        edit_bar = QHBoxLayout()
        edit_bar.addWidget(QLabel("Diff Preview:"))
        self.chk_edit = QCheckBox("Edit proposed code")
        self.chk_edit.setToolTip(
            "Edit only the replacement/created code, never the source/target. "
            "Save & Validate before applying.")
        edit_bar.addStretch(1)
        edit_bar.addWidget(self.chk_edit)
        diff_layout.addLayout(edit_bar)
        self.diff = DiffView()
        # Ctrl+F searches the diff; Ctrl+H replaces inside the PROPOSED code of the patches
        self.patch_replacer = _PatchReplacer(self)
        self.preview_splitter = QSplitter(Qt.Vertical)
        self.preview_splitter.addWidget(wrap_with_find_bar(self.diff, patch_replacer=self.patch_replacer))
        edit_container = QWidget()
        edit_layout = QVBoxLayout(edit_container)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_edit = flexible_label(QLabel("Editable replacement (project files are not modified here):"))
        edit_layout.addWidget(self.lbl_edit)
        self.edit_code = CodeEditor(read_only=False)
        self.edit_highlighter = CodeHighlighter(self.edit_code.document())
        edit_layout.addWidget(wrap_with_find_bar(self.edit_code, allow_replace=True), 1)
        edit_buttons = QHBoxLayout()
        self.btn_save_edit = QPushButton("Save & Validate")
        self.btn_discard_edit = QPushButton("Discard Draft")
        self.lbl_edit_status = flexible_label(QLabel(""))
        edit_buttons.addWidget(self.btn_save_edit)
        edit_buttons.addWidget(self.btn_discard_edit)
        edit_buttons.addWidget(self.lbl_edit_status, 1)
        edit_layout.addLayout(edit_buttons)
        self.preview_splitter.addWidget(edit_container)
        self.edit_container = edit_container
        self.edit_container.hide()
        self.preview_splitter.setStretchFactor(0, 3)
        self.preview_splitter.setStretchFactor(1, 2)
        diff_layout.addWidget(self.preview_splitter, 1)
        splitter.addWidget(diff_container)
        
        # 40/60 split favoring diff view (more important)
        splitter.setStretchFactor(0, 2)  # Changes list
        splitter.setStretchFactor(1, 3)  # Diff view
        layout.addWidget(splitter, 1)

        btns = QHBoxLayout()
        self.btn_clear = QPushButton("Clear Parsed Changes")
        self.btn_clear.setToolTip("Clear proposals and diff previews, preserving the original Response XML and applied history")
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setToolTip("Tick every valid change (also re-enables rejected ones)")
        self.btn_reject = QPushButton("Reject Selected Change")
        self.btn_reject_all = QPushButton("Reject All")
        self.btn_apply_sel = QPushButton("Apply Selected")
        self.btn_apply_all = QPushButton("Apply All")
        self.btn_dry = QPushButton("Dry Run")
        btns.addWidget(self.btn_clear)
        btns.addWidget(self.btn_select_all)
        btns.addWidget(self.btn_reject)
        btns.addWidget(self.btn_reject_all)
        btns.addStretch(1)
        btns.addWidget(self.btn_dry)
        btns.addWidget(self.btn_apply_all)
        btns.addWidget(self.btn_apply_sel)
        layout.addLayout(btns)

        self.list.itemChanged.connect(self._on_toggle)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_list_menu)
        self.chk_edit.toggled.connect(self._toggle_editor)
        self.edit_code.textChanged.connect(self._edit_text_changed)
        self.btn_save_edit.clicked.connect(self._save_edit_and_reload)
        self.btn_discard_edit.clicked.connect(self._discard_draft)
        self.btn_clear.clicked.connect(self._clear_parsed)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_reject.clicked.connect(self._reject)
        self.btn_reject_all.clicked.connect(self._reject_all)
        self.btn_apply_sel.clicked.connect(lambda: self._apply(all_first=False))
        self.btn_apply_all.clicked.connect(lambda: self._apply(all_first=True))
        self.btn_dry.clicked.connect(self._dry_run)

    # ------------------------------------------------------------------
    def reload(self):
        if self._in_reload:
            return
        if self._edit_change_object is not None and not any(
                c is self._edit_change_object for c in self.ctrl.changes):
            # A new Response may reuse an old change ID. Never apply the old
            # editor's draft to the new Response's different Change object.
            self._edit_change_id = None
            self._edit_change_object = None
            self._edit_initial_content = None
        if self._has_unsaved_draft() and not self._commit_pending_edit():
            return
        self._in_reload = True
        try:
            self._reload_contents()
        finally:
            self._in_reload = False

    def _reload_contents(self):
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
        notice = self.ctrl.response_notice
        if notice:
            is_warning = notice.type == "warning"
            heading = "AI warning (unverified): " if is_warning else "AI note: "
            self.lbl_note.setText(heading + notice.text)
            # QLabel may truncate unusually long notes to protect the layout.
            # Provide the complete plain-text note through its tooltip too.
            from html import escape
            self.lbl_note.setToolTip(escape(notice.text))
            self.lbl_note.setStyleSheet(
                "QLabel { background-color: #553a19; color: #fff1d3; "
                "border: 1px solid #bd883b; border-radius: 4px; padding: 8px; }"
                if is_warning else
                "QLabel { background-color: palette(base); color: palette(text); "
                "border: 1px solid palette(mid); border-radius: 4px; padding: 8px; }")
            self.lbl_note.setVisible(True)
        else:
            self.lbl_note.clear()
            self.lbl_note.setVisible(False)

        restore_row = None
        for row, ch in enumerate(self.ctrl.changes):
            label = f"[{'✓' if ch.selected else ' '}] {ch.path}  ·  {ch.display_operation}  ·  {ch.id}"
            extra = []
            if not ch.valid:
                extra.append(ch.validation_errors[0] if ch.validation_errors else "invalid")
            if ch.edited:
                extra.append("✎ edited")
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
            self._load_editor(None)
            return
        header = ""
        if ch.reason:
            header += f"# Reason: {ch.reason}\n"
        if ch.summary:
            header += f"# Summary: {ch.summary}\n"
        if ch.edited:
            header += "# NOTE: the proposed code was edited in the app (it differs from the LLM's response)\n"
        for w in getattr(ch, "warnings", []) or []:
            header += f"# WARNING: {w}\n"
        if ch.match_kind:
            header += f"# Located via: {ch.match_kind}\n"
        body = ch.unified_diff or f"# ({ch.operation}: no line diff for this operation)"
        self.diff.show_diff(header + body)
        self._load_editor(ch)

    def _load_editor(self, ch):
        """Load a change into the editor without disturbing an in-progress draft."""
        cid = ch.id if ch else None
        if (cid == self._edit_change_id and ch is self._edit_change_object
                and self._edit_initial_content == (ch.content if ch else None)):
            return
        self._edit_change_id = cid
        self._edit_change_object = ch
        self._edit_initial_content = ch.content if ch else None
        self.edit_code.blockSignals(True)
        self.edit_code.setPlainText(ch.content if ch and ch.content is not None else "")
        self.edit_code.blockSignals(False)
        self.edit_highlighter.set_language(filename=ch.path if ch else "")
        editable = ch is not None and ch.operation not in ("delete", "rename") and ch.content is not None
        self.chk_edit.setEnabled(editable)
        if not editable and self.chk_edit.isChecked():
            self.chk_edit.blockSignals(True)
            self.chk_edit.setChecked(False)
            self.chk_edit.blockSignals(False)
        self.edit_container.setVisible(editable and self.chk_edit.isChecked())
        self.lbl_edit_status.setText("")
        self._edit_text_changed()

    def _edit_text_changed(self):
        dirty = (self._edit_change_id is not None and self._edit_initial_content is not None
                 and self.edit_code.toPlainText() != self._edit_initial_content)
        self.btn_save_edit.setEnabled(dirty)
        self.btn_discard_edit.setEnabled(dirty)
        self.btn_apply_sel.setEnabled(not dirty)
        self.btn_apply_all.setEnabled(not dirty)
        self.btn_dry.setEnabled(not dirty)
        if dirty:
            self.lbl_edit_status.setText("Unsaved draft — Save & Validate to enable Apply")

    def _has_unsaved_draft(self) -> bool:
        return (self._edit_change_id is not None and self._edit_initial_content is not None
                and self.edit_code.toPlainText() != self._edit_initial_content)

    def _commit_pending_edit(self) -> bool:
        """Commit an in-memory proposal, revalidate all changes, NEVER write disk files.

        False means no new successful validation. The caller must check for a
        still-dirty draft before navigating or applying a patch.
        """
        if not self._has_unsaved_draft():
            return False
        text = self.edit_code.toPlainText()
        ch = self._edit_change_object
        if ch is None or ch.content is None or not any(c is ch for c in self.ctrl.changes):
            return False
        previous_content, previous_edited = ch.content, ch.edited
        try:
            self.ctrl.update_change_content(ch, text)
            problems = self.ctrl.validate_current_changes()
        except Exception as exc:
            # No unvalidated replacement can remain approved for Apply.
            ch.content = previous_content
            ch.edited = previous_edited
            ch.valid = False
            ch.selected = False
            ch.status = "invalid"
            ch.validation_errors = [f"Validation did not complete: {exc}"]
            self.main._report_ui_error("Validating edited proposal", exc)
            self.main.error_box(f"Could not validate edited proposal: {exc}")
            self._edit_text_changed()
            return False
        self._edit_initial_content = text
        self.lbl_edit_status.setText(
            "Validated" if not problems else f"Validation: {len(problems)} problem(s), review before apply")
        self.main.statusBar().showMessage(
            f"Saved proposed code and revalidated ({len(problems)} issue(s))", 5000)
        self._edit_text_changed()
        return True

    def _save_edit_and_reload(self):
        if self._commit_pending_edit():
            self.reload()

    def _discard_draft(self):
        ch = next((c for c in self.ctrl.changes if c.id == self._edit_change_id), None)
        self.edit_code.setPlainText(ch.content if ch and ch.content is not None else "")
        self._edit_initial_content = ch.content if ch else None
        self._edit_text_changed()
        self.lbl_edit_status.setText("Draft discarded")

    def _toggle_editor(self, enabled: bool):
        if not enabled and self._has_unsaved_draft():
            if self._commit_pending_edit():
                QTimer.singleShot(0, self.reload)
            elif self._has_unsaved_draft():
                # Validation failed: leave the draft visible, never discard it.
                self.chk_edit.blockSignals(True)
                self.chk_edit.setChecked(True)
                self.chk_edit.blockSignals(False)
                return
        self.edit_container.setVisible(enabled and self.chk_edit.isEnabled())
        if enabled:
            self.edit_code.setFocus()

    def _on_current_changed(self, *_):
        saved = self._commit_pending_edit()
        if self._has_unsaved_draft():
            # Stay on the draft if validation could not complete.
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) == self._edit_change_id:
                    self.list.blockSignals(True)
                    self.list.setCurrentRow(i)
                    self.list.blockSignals(False)
                    return
        self._show_diff()
        if saved and not self._in_reload:
            QTimer.singleShot(0, self.reload)

    def _clear_parsed(self):
        if not self.ctrl.changes and not self.ctrl.response_notice:
            return
        if QMessageBox.question(
            self, "Clear parsed changes",
            "Discard all parsed changes and preview diffs?\n\n"
            "Your original response XML, project files, and applied history will be kept.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._edit_change_id = None
        self._edit_change_object = None
        self._edit_initial_content = None
        self.ctrl.clear_parsed_changes()
        self.reload()
        self.main._refresh_state_label()
        self.main.statusBar().showMessage("Parsed changes cleared. Response XML retained.", 5000)

    def _on_double_click(self, item):
        cid = item.data(Qt.UserRole)
        ch = next((c for c in self.ctrl.changes if c.id == cid), None)
        if ch is None:
            return
        try:
            file_path = file_manager.safe_join(
                self.ctrl.project.root, ch.path,
                allow_external=self.ctrl.project.settings.external_paths)
        except file_manager.PathSafetyError as exc:
            self.main.error_box(str(exc))
            return
        open_in_code_editor(file_path, self)

    def _show_list_menu(self, point):
        item = self.list.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Open file in code editor").triggered.connect(
            lambda: self._on_double_click(item))
        menu.addAction("Choose another editor…").triggered.connect(
            lambda: self._choose_another_editor(item))
        exec_qt(menu, self.list.viewport().mapToGlobal(point))

    def _choose_another_editor(self, item):
        cid = item.data(Qt.UserRole)
        ch = next((c for c in self.ctrl.changes if c.id == cid), None)
        if ch is None:
            return
        try:
            file_path = file_manager.safe_join(
                self.ctrl.project.root, ch.path,
                allow_external=self.ctrl.project.settings.external_paths)
        except file_manager.PathSafetyError as exc:
            self.main.error_box(str(exc))
            return
        open_in_code_editor(file_path, self, force_choose=True)

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
        if self._has_unsaved_draft():
            self._commit_pending_edit()
            if self._has_unsaved_draft():
                return
            self.reload()
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
        if self._has_unsaved_draft():
            self._commit_pending_edit()
            if self._has_unsaved_draft():
                return
            self.reload()
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
            + "\n".join(f"  • {c.id} {c.display_operation} {c.path}" for c in selected)
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
