"""Main window: tabs (Project / Prompt / Response / Changes / History / Instructions / Run).

Fixes vs original:
- run() now correctly imports QApplication before creating MainWindow
- _tab_changed also refreshes the Changes tab when switching to it
- _sync_log uses the same 2000-line cap as the Controller
- BACKUPS_DIR is refreshed dynamically via ctrl._backups_dir()
- State label colour-codes the current state for quick scanning
"""
from __future__ import annotations

import os

from qtpy.QtCore import QSettings, Qt, QTimer, Signal
from qtpy.QtGui import QAction, QKeySequence, QTextCursor
from qtpy.QtWidgets import (QApplication, QComboBox, QDialog, QDockWidget, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from core.controller import APP_DIR, STORAGE_DIR, Controller, State
from core.qt_compat import exec_qt
from core.command_store import CommandStore
from core.diagnostics import record_error
from ui.app_icon import load_app_icon, set_windows_app_id
from ui.changes_view import ChangesView
from ui.code_editor import mono_font
from ui.find_bar import wrap_with_find_bar
from ui.layout_utils import fit_to_screen
from ui.text_wrap import apply_wrap
from ui.text_wrap import wrap_state
from ui.guide_view import GuideView
from ui.history_view import HistoryView
from ui.patch_view import InstructionsView
from ui.project_view import ProjectView
from ui.prompt_view import PromptView
from ui.response_view import ResponseView
from ui.settings_dialog import SettingsDialog

# Colour map for the state label
_STATE_COLOURS = {
    State.APPLIED:   "#1a7f37",   # green
    State.VALIDATED: "#1a7f37",
    State.ROLLED_BACK: "#1a7f37",
    State.DRAFT:     "#555555",   # neutral
    State.PROMPT_READY: "#0550ae",
    State.SENT:      "#0550ae",
    State.RESPONSE_RECEIVED: "#0550ae",
    State.PARSED:    "#0550ae",
    State.PREVIEW:   "#0550ae",
    State.APPROVED:  "#0550ae",
    State.INVALID:   "#c93c2d",   # red
    State.CONFLICT:  "#c93c2d",
    State.FAILED:    "#c93c2d",
    State.REJECTED:  "#7d4e00",   # amber
}


class MainWindow(QMainWindow):
    # Emitted from the worker thread; delivered (queued) in the GUI thread.
    _run_finished = Signal(int, str)
    _run_output = Signal(int, str, str)

    def __init__(self):
        super().__init__()
        self.ctrl = Controller()
        from core.qt_binding import selected_qt_api
        self.ctrl.log(f"Qt provider selected: {selected_qt_api() or 'unknown'}")
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(450)
        self._settings_save_timer.timeout.connect(self.ctrl.save_workspace_config)
        r0 = self.ctrl.project.root
        self._shown_root = os.path.normcase(os.path.abspath(r0)) if r0 else ""   # for on_project_opened()

        self.setWindowTitle("AI Patch Manager — LLM proposes · app validates · user approves")
        self.setWindowIcon(load_app_icon())
        fit_to_screen(self, 1240, 800)

        tabs = QTabWidget()
        self.project = ProjectView(self.ctrl, self)
        self.prompt = PromptView(self.ctrl, self)
        self.response_tab = ResponseView(self.ctrl, self)
        self.patch_tab = InstructionsView(self.ctrl, self)
        self.changes = ChangesView(self.ctrl, self)
        self.history = HistoryView(self.ctrl, self)
        self.run_tab = self._build_run_tab()
        tabs.addTab(self.project, "Project")
        tabs.addTab(self.prompt, "Prompt")
        tabs.addTab(self.response_tab, "Response")
        tabs.addTab(self.changes, "Changes / Preview")
        tabs.addTab(self.history, "History")
        tabs.addTab(self.patch_tab, "Instructions")
        tabs.addTab(self.run_tab, "Run")
        self.guide = GuideView(self.ctrl, self)
        tabs.addTab(self.guide, GuideView.TAB_TITLE)      # last tab: how to use the app
        self.tabs = tabs
        tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(tabs)

        # log dock (can be hidden / shown / cleared)
        self.log_dock = QDockWidget("Log", self)
        self.log_dock.setObjectName("log_dock")
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(4, 2, 4, 4)
        log_bar = QHBoxLayout()
        self.btn_log_clear = QPushButton("Clear Log")
        self.btn_log_hide = QPushButton("Hide")
        self.btn_log_clear.setToolTip("Remove all log lines")
        self.btn_log_hide.setToolTip("Hide the log panel (View ▸ Show Log brings it back)")
        log_bar.addWidget(self.btn_log_clear)
        log_bar.addWidget(self.btn_log_hide)
        log_bar.addStretch(1)
        log_layout.addLayout(log_bar)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        apply_wrap(self.log_view, True)
        log_layout.addWidget(wrap_with_find_bar(self.log_view), 1)
        self.log_dock.setWidget(log_panel)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.btn_log_clear.clicked.connect(self.clear_log)
        self.btn_log_hide.clicked.connect(lambda: self.log_dock.setVisible(False))

        # menu
        men = self.menuBar().addMenu("&File")
        act_open = QAction("Open Project...", self); act_open.triggered.connect(self.project._open)
        act_settings = QAction("Settings...", self); act_settings.triggered.connect(self.open_settings)
        act_reset = QAction("Reset Session", self); act_reset.triggered.connect(self._reset)
        act_quit = QAction("Quit", self); act_quit.triggered.connect(self.close)
        men.addAction(act_open)
        self.menu_recent = men.addMenu("Open &Recent")
        self.menu_recent.setToolTipsVisible(True)
        self.menu_recent.aboutToShow.connect(
            lambda: self.populate_recent_menu(self.menu_recent))
        for a in (act_settings, act_reset):
            men.addAction(a)
        men.addSeparator(); men.addAction(act_quit)

        view_menu = self.menuBar().addMenu("&View")
        self.act_toggle_log = self.log_dock.toggleViewAction()   # stays in sync with the dock's X button
        self.act_toggle_log.setText("Show Log")
        self.act_toggle_log.setShortcut(QKeySequence("Ctrl+Shift+L"))
        act_clear_log = QAction("Clear Log", self)
        act_clear_log.triggered.connect(self.clear_log)
        # Wrap Text: one setting for all code / text viewers (also a checkbox inside the file viewer / line picker)
        self.act_wrap = QAction("Wrap Text", self)
        self.act_wrap.setCheckable(True)
        self.act_wrap.setShortcut(QKeySequence("Alt+Z"))
        self.act_wrap.setToolTip("Wrap long lines in code, diff, prompt and instruction viewers (enabled by default)")
        self.act_wrap.setChecked(wrap_state().enabled())
        self.act_wrap.toggled.connect(wrap_state().set_enabled)
        wrap_state().changed.connect(self.act_wrap.setChecked)      # stays in sync with the dialogs' checkboxes
        view_menu.addAction(self.act_wrap)
        view_menu.addSeparator()
        view_menu.addAction(self.act_toggle_log)
        view_menu.addAction(act_clear_log)

        help_menu = self.menuBar().addMenu("&Help")
        act_guide = QAction("How to Use", self)
        act_guide.setShortcut(QKeySequence("F1"))
        act_guide.triggered.connect(lambda: self.tabs.setCurrentWidget(self.guide))
        help_menu.addAction(act_guide)

        # remembered between launches
        self._ui_settings = QSettings("AIPatchManager", "AIPatchManager")
        self._layout_restoring = True
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(500)
        self._layout_save_timer.timeout.connect(self._save_layout_preferences)
        self.project.tree.header().sectionResized.connect(self._schedule_layout_save)
        self.history.tree.header().sectionResized.connect(self._schedule_layout_save)
        self.changes.splitter.splitterMoved.connect(self._schedule_layout_save)
        self.changes.preview_splitter.splitterMoved.connect(self._schedule_layout_save)
        self.history.splitter.splitterMoved.connect(self._schedule_layout_save)
        QTimer.singleShot(0, self._restore_layout_preferences)
        self.log_dock.setVisible(
            self._ui_settings.value("log_visible", True, type=bool))
        self.log_dock.visibilityChanged.connect(self._remember_log_visibility)

        self.statusBar().showMessage("Ready. Open a project to begin.")
        self._state_lbl = QLabel("")
        self.btn_log_toggle = QPushButton("Log")
        self.btn_log_toggle.setCheckable(True)
        self.btn_log_toggle.setFlat(True)
        self.btn_log_toggle.setToolTip("Show / hide the log panel (Ctrl+Shift+L)")
        self.btn_log_toggle.setChecked(self.log_dock.isVisible())
        self.btn_log_toggle.toggled.connect(self.log_dock.setVisible)
        self.log_dock.visibilityChanged.connect(self.btn_log_toggle.setChecked)
        self.statusBar().addPermanentWidget(self.btn_log_toggle)
        self.statusBar().addPermanentWidget(self._state_lbl)
        self._refresh_state_label()
        self.ctrl.log("application started")
        self._sync_log()

    # ------------------------------------------------------------------
    def _build_run_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "Run user-defined commands in the project directory.\n"
            "Examples: pytest   |   python main.py   |   npm test"))
        
        # Saved commands section
        layout.addWidget(QLabel("Saved commands (click to use):"))
        self.cmd_list = QListWidget()
        self.cmd_list.setMaximumHeight(120)
        self.cmd_list.itemClicked.connect(self._on_cmd_list_clicked)
        layout.addWidget(self.cmd_list)
        
        # Save/Delete buttons for commands
        cmd_btn_row = QHBoxLayout()
        self.btn_save_cmd = QPushButton("Save Current Command")
        self.btn_delete_cmd = QPushButton("Delete Selected")
        self.btn_delete_cmd.setToolTip("Delete the command selected above")
        cmd_btn_row.addWidget(self.btn_save_cmd)
        cmd_btn_row.addWidget(self.btn_delete_cmd)
        cmd_btn_row.addStretch(1)
        layout.addLayout(cmd_btn_row)
        
        # Command input row
        row = QHBoxLayout()
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("pytest -x")
        self.btn_run = QPushButton("Run")
        self.btn_run_prev = QPushButton("Run Previous Command")
        self.btn_use_errors = QPushButton("Send output → Errors box")
        row.addWidget(self.cmd_input, 1)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_run_prev)
        layout.addLayout(row)
        
        # Output view
        self.out_view = QPlainTextEdit(); self.out_view.setReadOnly(True)
        self.out_view.setFont(mono_font(10))
        self.out_view.setMaximumBlockCount(12000)
        apply_wrap(self.out_view, True)
        self._run_streams_seen = set()
        self._run_generation = 0  # Ignore results from commands started in an earlier workspace.
        self._run_output_chars = 0
        self._run_output_limit = 2 * 1024 * 1024
        layout.addWidget(wrap_with_find_bar(self.out_view), 1)
        layout.addWidget(self.btn_use_errors)
        
        # Connections
        self._run_finished.connect(self._run_done)
        self._run_output.connect(self._append_run_output)
        self.btn_run.clicked.connect(self._run_cmd)
        self.btn_run_prev.clicked.connect(self._run_previous_cmd)
        self.btn_use_errors.clicked.connect(self._use_errors)
        self.cmd_input.returnPressed.connect(self._run_cmd)
        self.btn_save_cmd.clicked.connect(self._save_command)
        self.btn_delete_cmd.clicked.connect(self._delete_command)
        
        # Load saved commands
        self._load_saved_commands()
        self._update_prev_button()
        
        return w

    def _run_cmd(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        self._remember_last_command(cmd)
        import threading
        self._run_generation += 1
        run_id = self._run_generation
        run_cwd = self.ctrl.project.root or APP_DIR
        self.btn_run.setEnabled(False)
        self.btn_run_prev.setEnabled(False)
        self._run_streams_seen = set()
        header = f"$ {cmd}\n\n"
        self.out_view.setPlainText(header)
        self._run_output_chars = len(header)

        def worker():
            try:
                result = self.ctrl.run(
                    cmd, cwd=run_cwd,
                    on_output=lambda stream, chunk: self._run_output.emit(run_id, stream, chunk))
                summary = f"\n\n[exit code] {result.exit_code}  " \
                          f"[completed in {result.duration_seconds:.1f}s]"
                if result.output_truncated:
                    summary += "\n[returned output buffer was capped; the live view may also trim old output]"
            except Exception as exc:                     # never leave the UI stuck on "Running..."
                summary = f"\n\n[error] {exc}"
            self._run_finished.emit(run_id, summary)
        threading.Thread(target=worker, daemon=True).start()

    def _append_run_output(self, run_id: int, stream: str, chunk: str):
        if run_id != self._run_generation or not chunk:
            return
        prefix = ""
        if stream not in self._run_streams_seen:
            self._run_streams_seen.add(stream)
            prefix = f"[{stream}]\n"
        cursor = self.out_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(prefix + chunk)
        self.out_view.setTextCursor(cursor)
        self._run_output_chars = self.out_view.document().characterCount() - 1

        if self._run_output_chars > self._run_output_limit:
            # Trim in sizeable batches so a command that emits megabytes does
            # not make the UI retain an unbounded QTextDocument.
            trim_count = self._run_output_chars - (self._run_output_limit - 256 * 1024)
            trim = QTextCursor(self.out_view.document())
            trim.movePosition(QTextCursor.Start)
            trim.movePosition(QTextCursor.NextCharacter,
                              QTextCursor.KeepAnchor, max(0, trim_count))
            trim.removeSelectedText()
            trim.movePosition(QTextCursor.Start)
            trim.insertText("[Earlier command output trimmed to keep the view responsive.]\n")
            self._run_output_chars = self.out_view.document().characterCount() - 1

    def _run_done(self, run_id: int, text: str):
        if run_id != self._run_generation:
            return
        if not self._run_streams_seen and "[error]" not in text:
            text += "\n[no output]"
        cursor = self.out_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.out_view.setTextCursor(cursor)
        self.btn_run.setEnabled(True)
        self._update_prev_button()
        self._sync_log()

    # ------ "Run Previous Command" / saved workspace commands -------------------
    def _command_store(self) -> CommandStore:
        # Resolve per call: switching projects must never write into the old workspace.
        return CommandStore(self.ctrl.project.root if self.ctrl.project else "")

    def _command_error(self, operation: str, exc: Exception) -> None:
        self._report_ui_error(operation, exc)

    def _run_previous_cmd(self):
        cmd = self._last_command()
        if not cmd:
            self.statusBar().showMessage("No previous command yet.", 3000)
            return
        self.cmd_input.setText(cmd)
        self._run_cmd()

    def _last_command(self) -> str:
        if getattr(self, "_last_cmd", ""):
            return self._last_cmd
        try:
            return self._command_store().last_command()
        except ValueError as exc:
            self._command_error("Loading previous command failed", exc)
            return ""

    def _remember_last_command(self, cmd: str) -> None:
        self._last_cmd = cmd
        try:
            self._command_store().remember(cmd)
        except (OSError, ValueError) as exc:
            self._command_error("Saving previous command failed", exc)

    def _update_prev_button(self) -> None:
        cmd = self._last_command()
        self.btn_run_prev.setEnabled(bool(cmd) and self.btn_run.isEnabled())
        self.btn_run_prev.setToolTip(f"Re-run: {cmd}" if cmd else "No command has been run yet")

    def _use_errors(self):
        text = self.out_view.toPlainText().strip()
        if text:
            self.prompt.txt_errors.setPlainText(text)
            self.prompt.chk_errors.setChecked(True)
            self.tabs.setCurrentWidget(self.prompt)
            self.statusBar().showMessage("Command output copied into the Errors box.", 5000)

    def _load_saved_commands(self):
        try:
            commands = self._command_store().commands()
        except (OSError, ValueError) as exc:
            self._command_error("Loading saved commands failed", exc)
            commands = []
        self.cmd_list.clear()
        for cmd in commands:
            self.cmd_list.addItem(QListWidgetItem(cmd))

    def _on_cmd_list_clicked(self, item: QListWidgetItem):
        self.cmd_input.setText(item.text())

    def _save_command(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            self.statusBar().showMessage("Command is empty", 3000)
            return
        try:
            added = self._command_store().add(cmd)
        except (OSError, ValueError) as exc:
            self._command_error("Saving command failed", exc)
            return
        if added:
            self._load_saved_commands()
        self.statusBar().showMessage(f"✓ Saved: {cmd}" if added else "Command already saved", 3000)

    def _delete_command(self):
        item = self.cmd_list.currentItem()
        if not item:
            self.statusBar().showMessage("Select a command to delete", 3000)
            return
        try:
            removed = self._command_store().remove(item.text())
        except (OSError, ValueError) as exc:
            self._command_error("Deleting command failed", exc)
            return
        if removed:
            self._load_saved_commands()
            self.statusBar().showMessage(f"✓ Deleted: {item.text()}", 3000)

    # ------------------------------------------------------------------
    def open_settings(self):
        dlg = SettingsDialog(self.ctrl, self)
        exec_qt(dlg)
        self.prompt.reload()          # rule count / checkboxes reflect what was just edited
        self._sync_log()

    def _reset(self):
        self.ctrl.reset_session()
        self.changes.reload()
        self.response_tab.reload()
        self.prompt.reload()
        self._refresh_state_label()
        self._sync_log()

    def open_changes_tab(self):
        self.changes.reload()
        self.tabs.setCurrentWidget(self.changes)
        self._refresh_state_label()
        self._sync_log()

    def _report_ui_error(self, action: str, exc: Exception) -> None:
        """Surface recoverable errors, with a traceback available for debugging."""
        path = record_error(action, exc)
        self.ctrl.log(f"{action} failed: {type(exc).__name__}: {exc}")
        if hasattr(self, "log_view"):
            self._sync_log()
        message = f"{action} failed: {exc}"
        if path:
            message += f"  (details: {path})"
        self.statusBar().showMessage(message, 8000)

    def _refresh_tab(self, widget) -> None:
        if widget is self.project:
            self.project.reload()
        elif widget is self.prompt:
            self.prompt.reload()
        elif widget is self.response_tab:
            self.response_tab.reload()
        elif widget is self.changes:
            self.changes.reload()
        elif widget is self.history:
            self.history.reload()
        elif widget is self.patch_tab:
            self.patch_tab.reload()
        elif widget is self.run_tab:
            self._load_saved_commands()
            self._update_prev_button()

    def _tab_changed(self, index: int):
        """Refresh on demand, not eagerly for all tabs at project open."""
        widget = self.tabs.widget(index)
        if widget is None:
            return
        try:
            self._refresh_tab(widget)
        except Exception as exc:
            self._report_ui_error(f"Refreshing {type(widget).__name__}", exc)

    def update_prompt_stats(self):
        pass  # lightweight hook used by ProjectView

    def show_output(self, title: str, text: str):
        """Display arbitrary output without long lines expanding a QMessageBox off-screen."""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        fit_to_screen(dlg, 760, 520)
        layout = QVBoxLayout(dlg)
        viewer = QPlainTextEdit(dlg)
        viewer.setReadOnly(True)
        viewer.setFont(mono_font(10))
        apply_wrap(viewer, True)
        viewer.setPlainText(text or "[empty]")
        layout.addWidget(viewer, 1)
        buttons = QHBoxLayout()
        btn_copy = QPushButton("Copy", dlg)
        btn_close = QPushButton("Close", dlg)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(viewer.toPlainText()))
        btn_close.clicked.connect(dlg.accept)
        buttons.addStretch(1)
        buttons.addWidget(btn_copy)
        buttons.addWidget(btn_close)
        layout.addLayout(buttons)
        exec_qt(dlg)

    def error_box(self, text: str):
        self.show_output("Error", text)
        self._sync_log()

    def _refresh_state_label(self):
        state = self.ctrl.state
        colour = _STATE_COLOURS.get(state, "#555555")
        self._state_lbl.setText(
            f"  State: <b><span style='color:{colour}'>{state.value}</span></b>  "
        )
        self.statusBar().showMessage(f"State: {state.value}")

    def _sync_log(self):
        # Keep in sync with Controller's 2000-line limit
        self.log_view.setPlainText("\n".join(self.ctrl.log_lines[-2000:]))
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())                 # always show the newest line
        self._refresh_state_label()

    # ------ recent projects ----------------------------------------------
    @staticmethod
    def _short_path(path: str, limit: int = 60) -> str:
        home = os.path.expanduser("~")
        shown = ("~" + path[len(home):]) if path.startswith(home) else path
        if len(shown) > limit:
            shown = shown[:limit // 3] + " … " + shown[-(limit * 2 // 3):]
        return shown

    def populate_recent_menu(self, menu: QMenu) -> None:
        """Fill *menu* with the recent projects (File menu + Project tab button)."""
        menu.clear()
        entries = self.ctrl.recent.entries()
        if not entries:
            act = menu.addAction("(no recent projects)")
            act.setEnabled(False)
            return
        cur = (os.path.normcase(os.path.abspath(self.ctrl.project.root))
               if self.ctrl.project.root else "")
        for i, e in enumerate(entries, 1):
            name = e["name"].replace("&", "&&")
            label = f"{name}    —    {self._short_path(e['path']).replace('&', '&&')}"
            if not e["exists"]:
                label += "    (not found)"
            act = menu.addAction(f"&{i}  {label}" if i <= 9 else f"{i}  {label}")
            act.setToolTip(e["path"])
            act.setStatusTip(e["path"])
            if os.path.normcase(os.path.abspath(e["path"])) == cur:
                act.setCheckable(True)
                act.setChecked(True)              # the project that is open now
            act.triggered.connect(lambda _=False, p=e["path"]: self.open_recent(p))
        menu.addSeparator()
        act = menu.addAction("Clear Recent Projects")
        act.triggered.connect(self.clear_recent)

    def open_recent(self, path: str) -> None:
        if not os.path.isdir(path):
            ans = QMessageBox.question(
                self, "Project not found",
                f"This folder no longer exists:\n\n{path}\n\nRemove it from the recent list?")
            if ans == QMessageBox.Yes:
                self.ctrl.recent.remove(path)
            return
        self.project._open_path(path)

    def clear_recent(self) -> None:
        ans = QMessageBox.question(self, "Clear recent projects",
                                   "Remove all projects from the recent list?\n"
                                   "(Your projects and their data are not touched.)")
        if ans == QMessageBox.Yes:
            self.ctrl.recent.clear()
            self.statusBar().showMessage("Recent projects cleared.", 4000)

    def on_project_opened(self) -> None:
        """Reset project-bound UI state, then refresh only the visible tab.

        Inactive tabs will get fresh data through _tab_changed when visited;
        large history/diff documents are not loaded on every project switch.
        """
        root_now = (os.path.normcase(os.path.abspath(self.ctrl.project.root))
                    if self.ctrl.project.root else "")
        switched = root_now != getattr(self, "_shown_root", root_now)
        self._shown_root = root_now
        if switched:
            # Never leave the previous project's task, response, or command in
            # a hidden tab where it could accidentally be used on the new one.
            self.response_tab.txt_response.clear()
            self.prompt.txt_task.clear()
            self.prompt.txt_errors.clear()
            self.prompt.prompt_preview.clear()
            self._last_cmd = ""
            self._run_generation += 1   # queued output from the old project is now stale
            self.btn_run.setEnabled(True)
            self.cmd_input.clear()
            self.out_view.clear()
            self._load_saved_commands()
            self._update_prev_button()
        self.project._render_files(silent=True)
        current = self.tabs.currentWidget()
        if current is not self.project:
            try:
                self._refresh_tab(current)
            except Exception as exc:
                self._report_ui_error(f"Refreshing {type(current).__name__}", exc)
        root = self.ctrl.project.root
        base = "AI Patch Manager"
        self.setWindowTitle(f"{os.path.basename(root.rstrip('/' + os.sep))} — {base}"
                            if root else f"{base} — LLM proposes · app validates · user approves")
        self._refresh_state_label()
        self._sync_log()

    def schedule_workspace_save(self):
        """Coalesce rapid option toggles into one atomic config write."""
        self._settings_save_timer.start()

    def _schedule_layout_save(self, *_):
        if not self._layout_restoring:
            self._layout_save_timer.start()

    def _restore_layout_preferences(self):
        """Restore user-resized headers, split panes, and window dimensions."""
        objects = (
            ("layout/project_columns", self.project.tree.header()),
            ("layout/history_columns", self.history.tree.header()),
            ("layout/changes_splitter", self.changes.splitter),
            ("layout/changes_diff_editor", self.changes.preview_splitter),
            ("layout/history_splitter", self.history.splitter),
        )
        try:
            geometry = self._ui_settings.value("layout/window_geometry")
            if geometry:
                self.restoreGeometry(geometry)
            state = self._ui_settings.value("layout/window_state")
            if state:
                self.restoreState(state)
            for key, widget in objects:
                saved = self._ui_settings.value(key)
                if saved:
                    widget.restoreState(saved)
        except (TypeError, ValueError, RuntimeError) as exc:
            self._report_ui_error("Restoring UI layout", exc)
        finally:
            self._layout_restoring = False

    def _save_layout_preferences(self):
        """Persist only widget geometry; never overwrite per-project settings."""
        if self._layout_restoring:
            return
        self._ui_settings.setValue("layout/project_columns", self.project.tree.header().saveState())
        self._ui_settings.setValue("layout/history_columns", self.history.tree.header().saveState())
        self._ui_settings.setValue("layout/changes_splitter", self.changes.splitter.saveState())
        self._ui_settings.setValue("layout/changes_diff_editor", self.changes.preview_splitter.saveState())
        self._ui_settings.setValue("layout/history_splitter", self.history.splitter.saveState())
        self._ui_settings.setValue("layout/window_geometry", self.saveGeometry())
        self._ui_settings.setValue("layout/window_state", self.saveState())
        self._ui_settings.sync()

    def closeEvent(self, event):
        self._layout_save_timer.stop()
        self._save_layout_preferences()
        self.project.flush_pending_save()
        if self._settings_save_timer.isActive():
            self._settings_save_timer.stop()
            self.ctrl.save_workspace_config()
        super().closeEvent(event)

    def _remember_log_visibility(self, visible: bool):
        try:
            self._ui_settings.setValue("log_visible", bool(visible))
        except RuntimeError:
            pass                      # window is being destroyed (app shutdown)

    def clear_log(self):
        self.ctrl.clear_log()
        self.log_view.clear()


def run():
    import sys
    set_windows_app_id()                     # taskbar shows our icon (Windows)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("AI Patch Manager")
    app.setWindowIcon(load_app_icon())       # also used by every dialog
    win = MainWindow()
    win.show()
    sys.exit(exec_qt(app))
