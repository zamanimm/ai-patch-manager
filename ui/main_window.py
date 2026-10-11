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

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from core.controller import APP_DIR, STORAGE_DIR, Controller, State
from ui.changes_view import ChangesView
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
    # emitted from the worker thread; delivered (queued) in the GUI thread
    _run_finished = Signal(str)

    def __init__(self):
        super().__init__()
        self.ctrl = Controller()

        self.setWindowTitle("AI Patch Manager — LLM proposes · app validates · user approves")
        self.resize(1240, 800)

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
        log_layout.addWidget(self.log_view, 1)
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
        for a in (act_open, act_settings, act_reset):
            men.addAction(a)
        men.addSeparator(); men.addAction(act_quit)

        view_menu = self.menuBar().addMenu("&View")
        self.act_toggle_log = self.log_dock.toggleViewAction()   # stays in sync with the dock's X button
        self.act_toggle_log.setText("Show Log")
        self.act_toggle_log.setShortcut(QKeySequence("Ctrl+Shift+L"))
        act_clear_log = QAction("Clear Log", self)
        act_clear_log.triggered.connect(self.clear_log)
        view_menu.addAction(self.act_toggle_log)
        view_menu.addAction(act_clear_log)

        # remembered between launches
        self._ui_settings = QSettings("AIPatchManager", "AIPatchManager")
        self.log_dock.setVisible(
            self._ui_settings.value("log_visible", True, type=bool))
        self.log_dock.visibilityChanged.connect(
            lambda vis: self._ui_settings.setValue("log_visible", bool(vis)))

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
        layout.addWidget(self.out_view, 1)
        layout.addWidget(self.btn_use_errors)
        
        # Connections
        self._run_finished.connect(self._run_done)
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
        self.btn_run.setEnabled(False)
        self.btn_run_prev.setEnabled(False)
        self.out_view.setPlainText(f"$ {cmd}\n\nRunning...")

        def worker():
            try:
                result = self.ctrl.run(cmd)
                text = f"$ {cmd}\n\n{result.combined or '[no output]'}"
            except Exception as exc:                     # never leave the UI stuck on "Running..."
                text = f"$ {cmd}\n\n[error] {exc}"
            # NOTE: QTimer.singleShot from a plain thread never fires (no event
            # loop there) - a signal is the thread-safe way back to the GUI.
            self._run_finished.emit(text)
        threading.Thread(target=worker, daemon=True).start()

    def _run_done(self, text: str):
        self.out_view.setPlainText(text)
        self.btn_run.setEnabled(True)
        self._update_prev_button()
        self._sync_log()

    # ------ "Run Previous Command" ----------------------------------------
    def _run_previous_cmd(self):
        cmd = self._last_command()
        if not cmd:
            self.statusBar().showMessage("No previous command yet.", 3000)
            return
        self.cmd_input.setText(cmd)
        self._run_cmd()

    def _last_command(self) -> str:
        """Last command that was run (this session, else remembered on disk)."""
        return getattr(self, "_last_cmd", "") or self._read_cmd_store().get("last_command", "")

    def _remember_last_command(self, cmd: str) -> None:
        self._last_cmd = cmd
        data = self._read_cmd_store()
        data["last_command"] = cmd
        self._write_cmd_store(data)

    def _update_prev_button(self) -> None:
        cmd = self._last_command()
        self.btn_run_prev.setEnabled(bool(cmd) and self.btn_run.isEnabled())
        self.btn_run_prev.setToolTip(f"Re-run: {cmd}" if cmd else "No command has been run yet")

    def _use_errors(self):
        text = self.out_view.toPlainText().strip()
        if text:
            self.prompt.txt_errors.setPlainText(text)
            self.tabs.setCurrentWidget(self.prompt)
            self.statusBar().showMessage("Command output copied into the Errors box.", 5000)

    # ------ Saved commands management (stored in config directory) --------
    def _get_commands_file(self) -> str:
        """Get path to saved commands file in config directory."""
        if not self.ctrl.project or not self.ctrl.project.root:
            return ""
        import json
        workspace_dir = os.path.join(self.ctrl.project.root, ".ai-patch-manager")
        os.makedirs(workspace_dir, exist_ok=True)
        return os.path.join(workspace_dir, "saved_commands.json")
    
    def _read_cmd_store(self) -> dict:
        commands_file = self._get_commands_file()
        if not commands_file or not os.path.exists(commands_file):
            return {}
        try:
            import json
            with open(commands_file, 'r') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_cmd_store(self, data: dict) -> None:
        commands_file = self._get_commands_file()
        if not commands_file:
            return
        try:
            import json
            os.makedirs(os.path.dirname(commands_file), exist_ok=True)
            with open(commands_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.statusBar().showMessage(f"Error saving commands: {e}", 4000)

    def _load_commands_from_disk(self) -> list:
        """Load saved commands from config directory."""
        return list(self._read_cmd_store().get("commands", []))

    def _save_commands_to_disk(self, commands: list) -> None:
        """Save commands (keeps the remembered last command)."""
        data = self._read_cmd_store()
        data["commands"] = commands
        self._write_cmd_store(data)

    def _load_saved_commands(self):
        """Load saved commands from config directory and update listbox."""
        commands = self._load_commands_from_disk()
        self.cmd_list.clear()
        for cmd in commands:
            item = QListWidgetItem(cmd)
            self.cmd_list.addItem(item)
    
    def _on_cmd_list_clicked(self, item: QListWidgetItem):
        """When a saved command is clicked, populate the input field."""
        self.cmd_input.setText(item.text())

    def _save_command(self):
        """Save the current command to the saved commands list."""
        cmd = self.cmd_input.text().strip()
        if not cmd:
            self.statusBar().showMessage("Command is empty", 3000)
            return
        
        try:
            commands = self._load_commands_from_disk()
            # Add if not already present
            if cmd not in commands:
                commands.insert(0, cmd)  # Add to front (most recent)
                # Keep only last 20 commands
                commands = commands[:20]
                self._save_commands_to_disk(commands)
                self._load_saved_commands()
                self.statusBar().showMessage(f"✓ Saved: {cmd}", 3000)
            else:
                self.statusBar().showMessage("Command already saved", 3000)
        except Exception as e:
            self.statusBar().showMessage(f"Error saving command: {e}", 4000)

    def _delete_command(self):
        """Delete the selected command from the saved commands list."""
        item = self.cmd_list.currentItem()
        if not item:
            self.statusBar().showMessage("Select a command to delete", 3000)
            return
        
        try:
            cmd = item.text()
            commands = self._load_commands_from_disk()
            if cmd in commands:
                commands.remove(cmd)
                self._save_commands_to_disk(commands)
                self._load_saved_commands()
                self.statusBar().showMessage(f"✓ Deleted: {cmd}", 3000)
        except Exception as e:
            self.statusBar().showMessage(f"Error deleting command: {e}", 4000)

    # ------------------------------------------------------------------
    def open_settings(self):
        dlg = SettingsDialog(self.ctrl, self)
        dlg.exec()
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

    def _tab_changed(self, index: int):
        """Keep tab contents in sync with the current workspace state."""
        w = self.tabs.widget(index)
        try:
            # Auto-refresh all tabs when switched
            if w is self.project:
                self.project.reload()
            elif w is self.prompt:
                self.prompt.reload()
            elif w is self.response_tab:
                self.response_tab.reload()
            elif w is self.changes:
                self.changes.reload()
            elif w is self.history:
                self.history.reload()
            elif w is self.patch_tab:
                self.patch_tab.reload()
            elif w is self.run_tab:
                # Auto-load saved commands when Run tab is switched to
                self._load_saved_commands()
                self._update_prev_button()
        except Exception:
            pass

    def update_prompt_stats(self):
        pass  # lightweight hook used by ProjectView

    def show_output(self, title: str, text: str):
        dlg = QMessageBox(self)
        dlg.setWindowTitle(title)
        dlg.setTextFormat(Qt.PlainText)
        dlg.setText(text or "[empty]")
        dlg.exec()

    def error_box(self, text: str):
        QMessageBox.warning(self, "Error", text)
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

    def clear_log(self):
        self.ctrl.clear_log()
        self.log_view.clear()


def run():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
