"""Project tab: file explorer with per-file prompt mode.

Fixes vs original:
- _sync_selection now calls ctrl.save_workspace_config() so file
  selections persist across restarts.
- _mode_changed also saves the workspace config.
- Added "Select All" / "Deselect All" convenience buttons.
- lbl_project uses plain text (setTextFormat) to avoid accidental HTML
  injection from project names that contain special characters.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

MODES = ["full", "diff", "selection", "context"]


class ProjectView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.lbl_project = QLabel()
        self.btn_open = QPushButton("Open Project...")
        self.btn_refresh = QPushButton("Rescan Files")
        self.btn_rules = QPushButton("Rules...")
        self.btn_git = QPushButton("Git Status")
        top.addWidget(self.lbl_project, 1)
        top.addWidget(self.btn_open)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_rules)
        top.addWidget(self.btn_git)
        layout.addLayout(top)

        hint = QLabel(
            "<b>How to use:</b> Check files, pick mode (full/diff/selection/context). "
            "<br><b>For 'selection' or 'context' modes:</b> Select the file, then click "
            "<b>'Select Lines...'</b> to highlight which lines to include. "
            "Or double-click the file.")
        hint.setTextFormat(Qt.RichText)
        hint.setStyleSheet("color:#666;")
        layout.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Include", "File", "Mode"])
        self.tree.setColumnWidth(0, 70)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        bottom = QHBoxLayout()
        
        # Left side: bulk selection
        self.btn_select_all = QPushButton("Select All")
        self.btn_deselect_all = QPushButton("Deselect All")
        self.btn_select_all.setToolTip("Check all files")
        self.btn_deselect_all.setToolTip("Uncheck all files")
        bottom.addWidget(self.btn_select_all)
        bottom.addWidget(self.btn_deselect_all)
        
        # Separator
        sep = QLabel(" │ ")
        bottom.addWidget(sep)
        
        # Center: line range selection (most important for selection/context modes)
        self.btn_select_lines = QPushButton("Select Lines for File...")
        self.btn_select_lines.setToolTip(
            "For 'selection' or 'context' modes: click to choose specific line ranges to include.\n"
            "You can also double-click a file in the tree above.")
        self.btn_clear_ranges = QPushButton("Clear Line Selection")
        self.btn_clear_ranges.setToolTip("Clear any line range selection for the current file.")
        bottom.addWidget(self.btn_select_lines)
        bottom.addWidget(self.btn_clear_ranges)
        
        bottom.addStretch(1)
        layout.addLayout(bottom)

        self.tree.itemChanged.connect(lambda *_: self._sync_selection())
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        self.btn_open.clicked.connect(self._open)
        self.btn_refresh.clicked.connect(self._refresh)
        self.btn_rules.clicked.connect(self.main.open_settings)
        self.btn_git.clicked.connect(self._git)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_deselect_all.clicked.connect(self._deselect_all)
        self.btn_select_lines.clicked.connect(self._pick_ranges)
        self.btn_clear_ranges.clicked.connect(self._clear_ranges)

        self._refresh(silent=True)

    # ------------------------------------------------------------------
    def _open(self):
        from PySide6.QtWidgets import QFileDialog
        root = QFileDialog.getExistingDirectory(self, "Select project folder",
                                                self.ctrl.project.root or "")
        if root:
            self.ctrl.open_project(root)
            self._refresh()
            self.main.statusBar().showMessage(f"Opened {root}", 5000)

    def _git(self):
        self.main.show_output("Git status", self.ctrl.git_status())

    def _refresh(self, silent: bool = False):
        self.ctrl.refresh_files()
        root = self.ctrl.project.root or "(none — click Open Project)"
        self.lbl_project.setText(f"{root}")

        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            nodes = {}
            for rel in self.ctrl.files:
                parts = rel.split("/")
                cur_parent = self.tree.invisibleRootItem()
                path_acc = []
                for p in parts[:-1]:
                    path_acc.append(p)
                    key = "/".join(path_acc) + "/"
                    if key not in nodes:
                        item = QTreeWidgetItem(cur_parent, ["", p + "/", ""])
                        item.setData(0, Qt.UserRole, key)
                        nodes[key] = item
                    cur_parent = nodes[key]
                item = QTreeWidgetItem(cur_parent, ["", rel, ""])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                was_selected = rel in self.ctrl.selected_files
                item.setCheckState(0, Qt.Checked if was_selected else Qt.Unchecked)
                combo = QComboBox()
                combo.addItems(MODES)
                combo.setCurrentText(self.ctrl.selected_files.get(rel, "full"))
                self.tree.setItemWidget(item, 2, combo)
                item.setData(0, Qt.UserRole, rel)
                item.setData(1, Qt.UserRole, rel)
                combo.currentTextChanged.connect(
                    lambda mode, r=rel: self._mode_changed(r, mode))
                
                # Show line ranges if they exist for this file
                self._update_file_label(item, rel)
        finally:
            self.tree.blockSignals(False)
        if not silent:
            self.main.statusBar().showMessage(f"{len(self.ctrl.files)} files scanned", 4000)
        self._sync_selection(save=False)  # don't overwrite saved selection on refresh

    def _mode_changed(self, rel: str, mode: str):
        if rel in self.ctrl.selected_files:
            self.ctrl.selected_files[rel] = mode
            # FIX: persist mode change immediately
            try:
                self.ctrl.save_workspace_config()
            except Exception:
                pass

    # -- bulk selection helpers -----------------------------------------
    def _set_all_checked(self, checked: bool):
        self.tree.blockSignals(True)
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            stack.extend(item.child(i) for i in range(item.childCount()))
        self.tree.blockSignals(False)
        self._sync_selection()

    def _select_all(self):
        self._set_all_checked(True)

    def _deselect_all(self):
        self._set_all_checked(False)

    # -- line-range selection -------------------------------------------
    def _file_item_path(self, item) -> str:
        return item.data(1, Qt.UserRole) or ""

    def _current_file_rel(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        rel = self._file_item_path(item)
        return rel or None

    def _on_double_click(self, item, _col):
        rel = self._file_item_path(item)
        if not rel:
            return
        combo = self.tree.itemWidget(item, 2)
        mode = combo.currentText() if combo else "full"
        if mode in ("selection", "context"):
            self._open_range_dialog(rel)
        else:
            item.setExpanded(not item.isExpanded())

    def _update_file_label(self, item, rel: str):
        """Update file label to show if line ranges are selected."""
        ranges = self.ctrl.get_file_ranges(rel)
        text = rel
        if ranges:
            range_str = ", ".join(f"{s}-{e}" for s, e in ranges)
            text += f"  [lines: {range_str}]"
            item.setForeground(1, Qt.blue)  # Highlight to show selection active
        else:
            item.setForeground(1, Qt.black)
        item.setText(1, text)

    def _pick_ranges(self):
        rel = self._current_file_rel()
        if not rel:
            self.main.statusBar().showMessage(
                'Select a file first, then click "Select Lines..."', 4000)
            return
        self._open_range_dialog(rel)

    def _open_range_dialog(self, rel: str):
        root = self.ctrl.project.root
        if not root:
            self.main.statusBar().showMessage("Open a project first", 4000)
            return
        abs_path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(abs_path):
            self.main.statusBar().showMessage(f"File not found: {rel}", 4000)
            return
        from ui.code_select_dialog import CodeSelectDialog
        dlg = CodeSelectDialog(abs_path, rel,
                               initial=self.ctrl.get_file_ranges(rel),
                               parent=self)
        if dlg.exec() == CodeSelectDialog.Accepted:
            ranges = dlg.selected_ranges()
            self.ctrl.set_file_ranges(rel, ranges)
            pretty = ", ".join(f"{s}-{e}" for s, e in ranges) or "cleared"
            self.main.statusBar().showMessage(
                f"{rel}: selected lines {pretty} (saved to workspace)", 6000)
            
            # Update tree display to show the selection
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item and item.data(1, Qt.UserRole) == rel:
                    self._update_file_label(item, rel)
                    break
            
            self.main.update_prompt_stats()

    def _clear_ranges(self):
        rel = self._current_file_rel()
        if not rel:
            self.main.statusBar().showMessage("Select a file first", 4000)
            return
        self.ctrl.set_file_ranges(rel, [])
        
        # Update tree display to clear the selection indicator
        item = self.tree.currentItem()
        if item:
            self._update_file_label(item, rel)
        
        self.main.statusBar().showMessage(f"{rel}: line ranges cleared", 4000)
        self.main.update_prompt_stats()

    def _sync_selection(self, save: bool = True):
        sel = {}
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            rel = item.data(1, Qt.UserRole)
            if rel and item.checkState(0) == Qt.Checked:
                combo = self.tree.itemWidget(item, 2)
                sel[rel] = combo.currentText() if combo else "full"
            stack.extend(item.child(i) for i in range(item.childCount()))
        self.ctrl.selected_files = sel
        # FIX: persist selection so it survives restarts
        if save:
            try:
                self.ctrl.save_workspace_config()
            except Exception:
                pass
        self.main.update_prompt_stats()
