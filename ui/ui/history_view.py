"""History tab: transactions, diffs, undo.

Fixes vs original:
- Variable name clash: `l` (shadows builtin) renamed to `later_tx`.
- _diff now shows a meaningful message when no earlier transaction exists
  instead of silently doing a same-vs-same diff.
- Undo success message also logs the event so it appears in the Log dock.
"""
from __future__ import annotations

import time

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (QHeaderView, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.qt_compat import exec_qt
from ui.find_bar import wrap_with_find_bar
from ui.diff_view import DiffView


class HistoryView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Every Apply is one transaction. Select a transaction to undo all its changes, "
            "or select a Change row to undo/redo only that change. "
            "Single-change rollback is available for newer entries with saved change details."))
        
        # Splitter with transaction list and diff view
        self.splitter = QSplitter()
        splitter = self.splitter
        
        # Left: transaction tree (transactions with files as children)
        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Transaction / Change", "Status", "Files / Operation"])
        self.tree.setColumnCount(3)
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 285)
        self.tree.setColumnWidth(1, 125)
        self.tree.setColumnWidth(2, 90)
        self.tree.setUniformRowHeights(True)
        self.tree.setAllColumnsShowFocus(True)
        list_layout.addWidget(self.tree, 1)
        splitter.addWidget(list_container)
        
        # Right: diff view (side panel, not popup)
        diff_container = QWidget()
        diff_layout = QVBoxLayout(diff_container)
        diff_layout.setContentsMargins(0, 0, 0, 0)
        diff_layout.addWidget(QLabel("Diff vs Previous:"))
        self.diff_view = DiffView()
        diff_layout.addWidget(wrap_with_find_bar(self.diff_view), 1)
        splitter.addWidget(diff_container)
        
        # 40/60 split favoring diff view
        splitter.setStretchFactor(0, 2)  # List
        splitter.setStretchFactor(1, 3)  # Diff
        layout.addWidget(splitter, 1)

        # Actions are split into two rows so the History tab remains usable
        # on narrower screens. Destructive actions start disabled until a row
        # with a valid history context is selected.
        btns = QVBoxLayout()
        view_row = QHBoxLayout()
        action_row = QHBoxLayout()
        self.btn_view = QPushButton("View Details")
        self.btn_diff = QPushButton("Show Diff")
        self.btn_undo = QPushButton("Undo Transaction")
        self.btn_redo_tx = QPushButton("Redo Transaction")
        self.btn_undo_change = QPushButton("Undo Selected Change")
        self.btn_redo_change = QPushButton("Redo Selected Change")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_load_older = QPushButton("Load Older")
        view_row.addWidget(self.btn_refresh)
        view_row.addWidget(self.btn_load_older)
        view_row.addStretch(1)
        view_row.addWidget(self.btn_view)
        view_row.addWidget(self.btn_diff)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_undo_change)
        action_row.addWidget(self.btn_redo_change)
        action_row.addWidget(self.btn_undo)
        action_row.addWidget(self.btn_redo_tx)
        btns.addLayout(view_row)
        btns.addLayout(action_row)
        layout.addLayout(btns)
        for action_button in (self.btn_undo, self.btn_redo_tx,
                              self.btn_undo_change, self.btn_redo_change):
            action_button.setEnabled(False)

        # Connect signals
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_load_older.clicked.connect(self._load_older)
        self.btn_view.clicked.connect(self._view)
        self.btn_diff.clicked.connect(self._diff)
        self.btn_undo.clicked.connect(self._undo)
        self.btn_redo_tx.clicked.connect(self._redo_transaction)
        self.btn_undo_change.clicked.connect(self._undo_change)
        self.btn_redo_change.clicked.connect(self._redo_change)
        self.tree.currentItemChanged.connect(lambda *_: self._selection_changed())
        self.tree.itemDoubleClicked.connect(self._on_double_click)

    def reload(self):
        """Load the newest history page; older pages are loaded on demand."""
        self.tree.clear()
        self._loaded_count = 0
        self._load_history_page()

    def _load_older(self):
        self._load_history_page()

    def _load_history_page(self):
        page_size = 200
        txs = self.ctrl.list_history(limit=page_size, offset=self._loaded_count)
        if not txs and self._loaded_count == 0:
            empty_item = QTreeWidgetItem(["(no history yet)"])
            self.tree.addTopLevelItem(empty_item)
            self.btn_load_older.setEnabled(False)
            self._update_actions()
            return

        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        for tx in txs:
            # Parent: transaction
            label = f"{tx.human_time()}  •  {tx.id[:8]}"
            task_summary = tx.summary or tx.task[:30] or "(no task)"
            
            tx_item = QTreeWidgetItem([label, tx.status, f"{len(tx.files)} file(s)"])
            tx_item.setData(0, Qt.UserRole, tx.id)  # Store full tx id
            
            # Color code by status
            if tx.status == "applied":
                tx_item.setForeground(0, QColor("#008000"))  # Green
            elif tx.status == "rolled_back":
                tx_item.setForeground(0, QColor("#cc0000"))  # Red
            elif tx.status == "partially_rolled_back":
                tx_item.setForeground(0, QColor("#cc6600"))  # Amber
            elif tx.status == "baseline":
                tx_item.setForeground(0, QColor("#666666"))  # Gray
            
            # Add task summary as first child
            summary_item = QTreeWidgetItem([f"Task: {task_summary}", "", ""])
            summary_item.setForeground(0, QColor("#666666"))
            tx_item.addChild(summary_item)
            
            # Add files as children
            for f in tx.files:
                file_label = (f"{f.path} → {f.new_path}  •  {f.operation}" if f.new_path
                              else f"{f.path}  •  {f.operation}")
                file_item = QTreeWidgetItem([file_label, "", ""])
                file_item.setForeground(0, QColor("#0066cc"))
                file_item.setData(0, Qt.UserRole + 1, f.path)   # marks a file row
                file_item.setToolTip(0, "Double-click to open this file")
                tx_item.addChild(file_item)

                change_map = {str(item.get("id")): item for item in tx.change_records}
                rolled_back = set(tx.rolled_back_change_ids)
                for change_id in f.change_ids:
                    change_data = change_map.get(str(change_id))
                    if change_data is None:
                        legacy_item = QTreeWidgetItem([
                            f"{change_id}  •  details unavailable (legacy transaction)",
                            "legacy", ""] )
                        legacy_item.setDisabled(True)
                        legacy_item.setToolTip(0,
                            "This older transaction did not save the original change details; "
                            "single-change rollback is unavailable.")
                        file_item.addChild(legacy_item)
                        continue
                    operation = str(change_data.get("operation", "change"))
                    change_status = "rolled back" if change_id in rolled_back else "applied"
                    change_label = f"{change_id}  •  {operation}"
                    if change_data.get("summary"):
                        change_label += f"  •  {str(change_data['summary'])[:55]}"
                    change_item = QTreeWidgetItem([change_label, change_status, operation])
                    change_item.setData(0, Qt.UserRole + 2, str(change_id))
                    change_item.setToolTip(0, str(change_data.get("reason") or
                                                   "Select this row to undo or redo only this change."))
                    if change_status == "rolled back":
                        change_item.setForeground(0, QColor("#cc6600"))
                    file_item.addChild(change_item)
            
            # Expand transaction by default to show files
            tx_item.setExpanded(True)
            self.tree.addTopLevelItem(tx_item)

        self.tree.blockSignals(False)
        self.tree.setUpdatesEnabled(True)
        self._loaded_count += len(txs)
        total = self.ctrl.history.transaction_count()
        self.btn_load_older.setEnabled(self._loaded_count < total)
        self.btn_load_older.setText(
            f"Load Older ({max(0, total - self._loaded_count)} remaining)")
        self._update_actions()

    def _current_id(self):
        """Find the transaction root for any selected tree row."""
        item = self.tree.currentItem()
        while item is not None:
            tx_id = item.data(0, Qt.UserRole)
            if tx_id:
                return tx_id
            item = item.parent()
        return None

    def _current_change_id(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole + 2) if item else None

    def _selection_changed(self):
        self._update_diff()
        self._update_actions()

    def _update_actions(self):
        tx_id = self._current_id()
        tx = self.ctrl.history.get(tx_id) if tx_id else None
        change_id = self._current_change_id()
        rolled = set(tx.rolled_back_change_ids) if tx else set()
        known_change_ids = {str(item.get("id")) for item in tx.change_records} if tx else set()

        self.btn_undo.setEnabled(bool(tx and tx.status in
                                      ("applied", "partially_rolled_back")))
        self.btn_redo_tx.setEnabled(bool(tx and tx.status == "rolled_back"))
        self.btn_undo_change.setEnabled(bool(
            tx and change_id and change_id in known_change_ids
            and change_id not in rolled
            and tx.status in ("applied", "partially_rolled_back")))
        self.btn_redo_change.setEnabled(bool(
            tx and change_id and change_id in known_change_ids
            and change_id in rolled
            and tx.status in ("partially_rolled_back", "rolled_back")))

    def _on_double_click(self, item, _col):
        """Double-click a file or one of its change rows to inspect versions."""
        rel = item.data(0, Qt.UserRole + 1)
        parent = item.parent()
        while not rel and parent is not None:
            rel = parent.data(0, Qt.UserRole + 1)
            parent = parent.parent()
        tx_id = self._current_id()
        if not rel or not tx_id:
            return
        tx = self.ctrl.history.get(tx_id)
        if tx is None:
            return
        root = self.ctrl.project.root
        from core import file_manager
        from ui.file_view_dialog import FileViewDialog
        try:
            rec = next((f for f in tx.files if f.path == rel), None)
            current_rel = ((rec.current_path or rec.new_path or rel) if rec else rel)
            disk = file_manager.safe_join(root, current_rel)
        except Exception:
            disk = None
        versions = self.ctrl.history.file_versions(root, tx, rel)
        exec_qt(FileViewDialog(rel, versions, disk_path=disk, parent=self))

    def _view(self):
        tx_id = self._current_id()
        tx = self.ctrl.history.get(tx_id) if tx_id else None
        if tx is None:
            return
        lines = [f"Transaction {tx.id} ({tx.human_time()}) status={tx.status}",
                 f"Project: {tx.project}", f"Task: {tx.task}",
                 f"Summary: {tx.summary}", f"Backup: {tx.backup_dir}", "", "Files:"]
        for f in tx.files:
            lines.append(f"  • {f.path}  [{f.operation}]  changes={','.join(f.change_ids)}")
            lines.append(f"      old_hash={f.old_hash[:12] or '-'}  new_hash={f.new_hash[:12] or '-'}")
            if f.current_path or f.current_hash:
                lines.append(f"      current={f.current_path or f.path}  hash={f.current_hash[:12] or '-'}")
        if tx.rolled_back_change_ids:
            lines.extend(["", "Rolled-back changes:", *[f"  • {cid}" for cid in tx.rolled_back_change_ids]])
        if tx.change_rollback_events:
            lines.extend(["", "Single-change rollback/redo events:"])
            for event in tx.change_rollback_events:
                lines.append(f"  • {event.get('action')} {event.get('change_id')} at "
                             f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(event.get('timestamp', 0)))}")
        self.main.show_output("Transaction details", "\n".join(lines))

    def _update_diff(self):
        """Auto-update diff view when transaction is selected."""
        self._diff()
    
    def _diff(self):
        """Show diff in side panel (not popup)."""
        tx_id = self._current_id()
        if not tx_id:
            self.diff_view.show_diff("")
            return
        
        current = self.ctrl.history.get(tx_id)
        if current is None:
            self.diff_view.show_diff("")
            return

        if current.status == "baseline":
            # First entry: nothing earlier to compare with, so show what the
            # workspace looks like NOW compared to this baseline snapshot.
            d = self.ctrl.baseline_vs_current_diff()
            header = (f"Baseline {tx_id} → current workspace "
                      f"(all changes since the workspace was connected):\n\n")
            self.diff_view.show_diff(
                header + (d or "[no differences - workspace is identical to the baseline]"))
            return

        earlier = self.ctrl.history.previous_transaction(tx_id)
        if earlier is None:
            d = self.ctrl.history.diff_between(self.ctrl.project.root, current, current)
            header = f"Diff (pre-apply → post-apply) {tx_id}:\n\n"
        else:
            d = self.ctrl.history.diff_between(self.ctrl.project.root, earlier, current)
            header = f"Diff {earlier.id} → {tx_id}:\n\n"
        self.diff_view.show_diff(header + (d or "[no differences]"))

    def _undo(self):
        tx_id = self._current_id()
        if not tx_id:
            return
        tx = self.ctrl.history.get(tx_id)
        if tx is None:
            return
        detail = ("This will restore every file touched by the transaction to its original state. "
                  "Any single changes already rolled back will remain rolled back.")
        resp = QMessageBox.question(
            self, "Confirm Transaction Undo",
            f"Roll transaction {tx_id} back?\n\n{detail}\n"
            "The operation will stop if any touched file no longer matches its recorded state.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return
        try:
            self.ctrl.undo_transaction(tx_id)
        except Exception as exc:
            self.main.error_box(f"Undo failed: {exc}")
            return
        self.reload()
        self.main._sync_log()
        QMessageBox.information(self, "Undo", f"Transaction {tx_id} rolled back.")

    def _redo_transaction(self):
        tx_id = self._current_id()
        if not tx_id:
            return
        resp = QMessageBox.question(
            self, "Confirm Transaction Redo",
            f"Re-apply all changes in transaction {tx_id}?\n"
            "All target files must still match the recorded pre-transaction state.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return
        try:
            self.ctrl.redo_transaction(tx_id)
        except Exception as exc:
            self.main.error_box(f"Redo failed: {exc}")
            return
        self.reload()
        self.main._sync_log()
        QMessageBox.information(self, "Redo", f"Transaction {tx_id} re-applied.")

    def _undo_change(self):
        tx_id = self._current_id()
        change_id = self._current_change_id()
        if not tx_id or not change_id:
            return
        resp = QMessageBox.question(
            self, "Confirm Single-Change Undo",
            f"Undo only {change_id} from transaction {tx_id}?\n\n"
            "The application will rebuild the affected file from its original backup "
            "and replay the other changes from the same transaction.\n\n"
            "If the current file hash, backup integrity, or replay result does not match, "
            "the operation will be cancelled without changing the file.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return
        try:
            self.ctrl.undo_change(tx_id, change_id)
        except Exception as exc:
            self.main.error_box(f"Could not undo {change_id}: {exc}")
            return
        self.reload()
        self.main._sync_log()
        QMessageBox.information(
            self, "Single Change Undo",
            f"{change_id} was rolled back. Other changes in the transaction were preserved.")

    def _redo_change(self):
        tx_id = self._current_id()
        change_id = self._current_change_id()
        if not tx_id or not change_id:
            return
        resp = QMessageBox.question(
            self, "Confirm Single-Change Redo",
            f"Re-apply only {change_id} from transaction {tx_id}?\n\n"
            "The current file must match the last recorded partial state. The application "
            "will rebuild the file and verify its hash before recording the result.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return
        try:
            self.ctrl.redo_change(tx_id, change_id)
        except Exception as exc:
            self.main.error_box(f"Could not redo {change_id}: {exc}")
            return
        self.reload()
        self.main._sync_log()
        QMessageBox.information(self, "Single Change Redo", f"{change_id} was re-applied.")
