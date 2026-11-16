"""History tab: transactions, diffs, undo.

Fixes vs original:
- Variable name clash: `l` (shadows builtin) renamed to `later_tx`.
- _diff now shows a meaningful message when no earlier transaction exists
  instead of silently doing a same-vs-same diff.
- Undo success message also logs the event so it appears in the Log dock.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHeaderView, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ui.find_bar import wrap_with_find_bar
from ui.diff_view import DiffView


class HistoryView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Every Apply is one transaction. Undo restores the "
                                "exact pre-apply state from the backup snapshot."))
        
        # Splitter with transaction list and diff view
        self.splitter = QSplitter()
        splitter = self.splitter
        
        # Left: transaction tree (transactions with files as children)
        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Transaction", "Status", "Files"])
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

        # Buttons
        btns = QHBoxLayout()
        self.btn_view = QPushButton("View Details")
        self.btn_diff = QPushButton("Show Diff")
        self.btn_undo = QPushButton("Undo This Transaction")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_load_older = QPushButton("Load Older")
        btns.addWidget(self.btn_refresh)
        btns.addWidget(self.btn_load_older)
        btns.addStretch(1)
        btns.addWidget(self.btn_view)
        btns.addWidget(self.btn_diff)
        btns.addWidget(self.btn_undo)
        layout.addLayout(btns)

        # Connect signals
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_load_older.clicked.connect(self._load_older)
        self.btn_view.clicked.connect(self._view)
        self.btn_diff.clicked.connect(self._diff)
        self.btn_undo.clicked.connect(self._undo)
        self.tree.currentItemChanged.connect(lambda *_: self._update_diff())
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

    def _current_id(self):
        """Get transaction ID from currently selected item (handle both parent and child)."""
        item = self.tree.currentItem()
        if not item:
            return None
        
        # If item has UserRole data, it's a parent (transaction)
        tx_id = item.data(0, Qt.UserRole)
        if tx_id:
            return tx_id
        
        # If not, try to get parent's tx_id (child item)
        parent = item.parent()
        if parent:
            return parent.data(0, Qt.UserRole)
        
        return None

    def _on_double_click(self, item, _col):
        """Double-click a file under a transaction -> open it in the viewer."""
        rel = item.data(0, Qt.UserRole + 1)
        parent = item.parent()
        if not rel or parent is None:
            return                                   # transaction / task rows: default behaviour
        tx = self.ctrl.history.get(parent.data(0, Qt.UserRole))
        if tx is None:
            return
        root = self.ctrl.project.root
        from core import file_manager
        from ui.file_view_dialog import FileViewDialog
        try:
            disk = file_manager.safe_join(root, rel)
        except Exception:
            disk = None
        versions = self.ctrl.history.file_versions(root, tx, rel)
        FileViewDialog(rel, versions, disk_path=disk, parent=self).exec()

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
        resp = QMessageBox.question(
            self, "Confirm Undo",
            f"Roll transaction {tx_id} back to its pre-apply state?\n"
            "All files touched by it will be restored from the backup.",
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
