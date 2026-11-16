"""Project tab: file explorer with per-file prompt mode.

The startup scan restores the file tree before it is rendered, so selections
loaded from the per-workspace config remain visible after application restart.
Selection writes are debounced during use and flushed unconditionally when the
application closes or the user switches projects.
"""
from __future__ import annotations

import os

from qtpy.QtCore import Qt, QTimer, Signal, QUrl
from core.qt_compat import exec_qt
from ui.layout_utils import flexible_label
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (QComboBox, QMenu, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QStyledItemDelegate, QAbstractItemView,
                               QVBoxLayout, QWidget)

MODES = ["full", "diff", "selection", "context"]


class ModeDelegate(QStyledItemDelegate):
    """One on-demand editor for the mode column; no widget per file."""

    def createEditor(self, parent, option, index):
        if index.column() != 2 or not index.data(Qt.UserRole):
            return None
        editor = QComboBox(parent)
        editor.addItems(MODES)
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data(Qt.EditRole) or "full")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)


class ScanSignals(QWidget):
    """Thread-safe handoff from a background filesystem scan to the GUI."""
    finished = Signal(int, str, object, str)



class ProjectView(QWidget):
    def __init__(self, controller, main_window):
        super().__init__()
        self.ctrl = controller
        self.main = main_window

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.lbl_project = flexible_label(QLabel(), selectable=True)
        self.lbl_project.setTextFormat(Qt.PlainText)
        self.btn_open = QPushButton("Open Project...")
        self.btn_show_directory = QPushButton("Open Folder")
        self.btn_show_directory.setToolTip("Open the current project directory in the file manager")
        self.btn_recent = QPushButton("Recent")
        self.btn_recent.setToolTip("Reopen a recently used project")
        self.menu_recent = QMenu(self.btn_recent)
        self.menu_recent.setToolTipsVisible(True)
        self.menu_recent.aboutToShow.connect(
            lambda: self.main.populate_recent_menu(self.menu_recent))
        self.btn_recent.setMenu(self.menu_recent)
        self.btn_refresh = QPushButton("Rescan Files")
        self.btn_rules = QPushButton("Rules...")
        self.btn_git = QPushButton("Git Status")
        top.addWidget(self.lbl_project, 1)
        top.addWidget(self.btn_open)
        top.addWidget(self.btn_show_directory)
        top.addWidget(self.btn_recent)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_rules)
        top.addWidget(self.btn_git)
        layout.addLayout(top)

        hint = QLabel(
            "<b>How to use:</b> Check files, pick mode (full/diff/selection/context). "
            "<br><b>For 'selection' or 'context' modes:</b> Select the file, then click "
            "<b>'Select Lines...'</b> to highlight which lines to include. "
            "<br><b>Double-click</b> a file: full / diff mode opens it for viewing; "
            "selection / context mode opens the line picker.")
        hint.setTextFormat(Qt.RichText)
        flexible_label(hint)
        hint.setStyleSheet("color:#666;")
        layout.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Include", "File", "Mode"])
        self.tree.setItemDelegateForColumn(2, ModeDelegate(self.tree))
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._scan_signals = ScanSignals(self)
        self._scan_signals.finished.connect(self._scan_finished)
        self._scan_id = 0
        # On application startup Controller has restored the project root and
        # workspace selections, but it has NOT scanned the filesystem yet.
        # Do not render an empty tree and overwrite the restored selections.
        self._tree_ready = False
        self._scan_pending = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(450)
        self._save_timer.timeout.connect(self._save_selection_now)
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 75)
        self.tree.setColumnWidth(1, 550)
        self.tree.setColumnWidth(2, 145)
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
            "You can also double-click a file (in selection / context mode) in the tree above.")
        self.btn_clear_ranges = QPushButton("Clear Line Selection")
        self.btn_clear_ranges.setToolTip("Clear any line range selection for the current file.")
        bottom.addWidget(self.btn_select_lines)
        bottom.addWidget(self.btn_clear_ranges)
        
        bottom.addStretch(1)
        layout.addLayout(bottom)

        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        self.btn_open.clicked.connect(self._open)
        self.btn_show_directory.clicked.connect(self._open_project_folder)
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
        from qtpy.QtWidgets import QFileDialog
        root = QFileDialog.getExistingDirectory(self, "Select project folder",
                                                self.ctrl.project.root or "")
        if root:
            self._open_path(root)

    def _open_project_folder(self):
        root = self.ctrl.project.root
        if not root or not os.path.isdir(root):
            self.main.statusBar().showMessage("Open a project first", 4000)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(root))):
            self.main.error_box(f"Could not open project directory:\n{root}")

    def _open_path(self, root: str):
        """Open *root* (from the folder dialog or the recent list)."""
        self.flush_pending_save()
        if self.main._settings_save_timer.isActive():
            self.main._settings_save_timer.stop()
            self.ctrl.save_workspace_config()
        self._scan_id += 1  # invalidate any scan from the previous project
        try:
            self.ctrl.open_project(root)
            self._tree_ready = True  # Controller.open_project performs a synchronous scan.
            self._scan_pending = False
        except Exception as exc:
            self.main._report_ui_error("Opening project", exc)
            self.main.show_output("Project open failed", str(exc))
            return
        self.main.on_project_opened()
        self.main.statusBar().showMessage(f"Opened {root}", 5000)

    def _git(self):
        self.main.show_output("Git status", self.ctrl.git_status())

    def _refresh(self, silent: bool = False):
        # A project restored from config at application startup is already
        # open in Controller, but Controller.__init__ has not populated
        # ctrl.files. The old implementation always called refresh_files()
        # here; the Phase 1 optimization accidentally skipped that scan for
        # silent startup refreshes, leaving an empty tree and losing the
        # in-memory selection state. Scan in the background on first launch.
        if self.ctrl.project.is_open():
            if self._scan_pending:
                return
            if not silent or not self._tree_ready:
                self._start_scan()
                return
        self._render_files(silent=silent)

    def reload(self):
        """Refresh the explorer, scanning first if startup has not populated it."""
        if self.ctrl.project.is_open() and not self._tree_ready:
            if not self._scan_pending:
                self._start_scan()
            return
        self._render_files(silent=True)

    def _start_scan(self):
        import threading
        from core import scanner
        from core.controller import WORKSPACE_DIR_NAME
        root = self.ctrl.project.root
        include = list(self.ctrl.project.include)
        exclude = list(self.ctrl.project.exclude)
        if WORKSPACE_DIR_NAME not in " ".join(exclude):
            exclude.append(f"**/{WORKSPACE_DIR_NAME}/**")
        self._scan_id += 1
        request = self._scan_id
        self._scan_pending = True
        self.btn_refresh.setEnabled(False)
        self.main.statusBar().showMessage("Scanning project files…")

        def work():
            try:
                paths = scanner.scan_project(root, include, exclude)
                error = ""
            except Exception as exc:
                paths, error = [], str(exc)
            self._scan_signals.finished.emit(request, root, paths, error)

        threading.Thread(target=work, daemon=True).start()

    def _scan_finished(self, request, root, files, error):
        if request != self._scan_id or root != self.ctrl.project.root:
            return  # stale result from a project that has since changed
        self._scan_pending = False
        self.btn_refresh.setEnabled(True)
        if error:
            self.ctrl.log(f"project scan failed: {error}")
            self.main._sync_log()
            self.main.statusBar().showMessage(f"Scan failed: {error}", 7000)
            return
        self.ctrl.files = files
        self._tree_ready = True
        self.ctrl.log(f"scanned {len(files)} files")
        self._render_files(silent=False)

    def _render_files(self, silent=False):
        root = self.ctrl.project.root or "(none — click Open Project)"
        self.lbl_project.setText(f"{root}")
        self.lbl_project.setToolTip(root)
        self.btn_show_directory.setEnabled(bool(self.ctrl.project.root) and
                                            os.path.isdir(self.ctrl.project.root))

        self.tree.blockSignals(True)
        self.tree.setUpdatesEnabled(False)
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
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
                was_selected = rel in self.ctrl.selected_files
                item.setCheckState(0, Qt.Checked if was_selected else Qt.Unchecked)
                item.setData(0, Qt.UserRole, rel)
                item.setData(1, Qt.UserRole, rel)
                item.setData(2, Qt.UserRole, rel)
                item.setText(2, self.ctrl.selected_files.get(rel, "full"))
                
                # Show line ranges if they exist for this file
                self._update_file_label(item, rel)
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)
        if not silent:
            self.main.statusBar().showMessage(f"{len(self.ctrl.files)} files scanned", 4000)
        self._sync_selection(save=False)  # don't overwrite saved selection on refresh

    def _item_changed(self, item, column):
        if column == 2:
            rel = self._file_item_path(item)
            if rel and item.text(2) not in MODES:
                item.setText(2, "full")
        self._sync_selection()

    def _on_item_clicked(self, item, column):
        if column == 2 and self._file_item_path(item):
            self.tree.editItem(item, 2)

    def _save_selection_now(self):
        try:
            self.ctrl.save_workspace_config()
        except OSError as exc:
            self.main.statusBar().showMessage(f"Could not save selection: {exc}", 5000)

    def flush_pending_save(self):
        """Persist the visible selection before project switches or app exit.

        Always save, not only when the debounce timer happens to be active.
        During the first background scan the tree is not authoritative yet;
        in that case keep the selections already restored by Controller.
        """
        if self._save_timer.isActive():
            self._save_timer.stop()
        if self._tree_ready and not self._scan_pending:
            self._sync_selection(save=False)
        try:
            self.ctrl.save_workspace_config()
        except Exception as exc:
            self.ctrl.log(f"could not flush selected files: {exc}")
            try:
                self.main.statusBar().showMessage(
                    f"Could not save selected files: {exc}", 6000)
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
        """Double-click a file:
        - mode 'selection' / 'context' -> line-range picker (as before)
        - mode 'full' / 'diff'         -> open the file in the viewer
        """
        rel = self._file_item_path(item)
        if not rel:
            return                                   # folder rows keep expand/collapse
        mode = item.text(2) or "full"
        if mode in ("selection", "context"):
            self._open_range_dialog(rel)
        else:
            self._open_file_viewer(rel)

    def _open_file_viewer(self, rel: str):
        root = self.ctrl.project.root
        if not root:
            self.main.statusBar().showMessage("Open a project first", 4000)
            return
        from core import file_manager
        from ui.file_view_dialog import FileViewDialog
        abs_path = file_manager.safe_join(root, rel)
        if not os.path.isfile(abs_path):
            self.main.statusBar().showMessage(f"File not found: {rel}", 4000)
            return
        from ui.file_view_dialog import read_preview_text
        dialog = FileViewDialog(rel, [("Current file", read_preview_text(abs_path))],
                                 disk_path=abs_path, parent=self)
        exec_qt(dialog)

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
        if exec_qt(dlg) == CodeSelectDialog.Accepted:
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
        # Keep selected paths that are not represented in the current scan
        # (e.g. intentionally excluded/hidden/large files). They remain valid
        # selections and must not be silently discarded by a tree refresh.
        visible = set(self.ctrl.files or [])
        sel = {}
        root = self.ctrl.project.root
        if root:
            for rel, mode in self.ctrl.selected_files.items():
                if rel not in visible:
                    try:
                        from core.file_manager import safe_join
                        candidate = safe_join(root, rel)
                        if os.path.isfile(candidate):
                            sel[rel] = mode if mode in MODES else "full"
                    except Exception:
                        continue

        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            rel = item.data(1, Qt.UserRole)
            if rel and item.checkState(0) == Qt.Checked:
                sel[rel] = item.text(2) if item.text(2) in MODES else "full"
            stack.extend(item.child(i) for i in range(item.childCount()))
        self.ctrl.selected_files = sel
        if save:
            self._save_timer.start()
        self.main.update_prompt_stats()
