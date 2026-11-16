"""Read-only file viewer opened by double-clicking a file in a list.

Shows one or more VERSIONS of a file (e.g. in History: after / before the
transaction / current workspace file) with a selector when there are several.
Viewing never modifies anything. An explicit button hands the on-disk file to
the operating system's default application, or to a program you choose ("Open with…").
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QPushButton, QSizePolicy, QVBoxLayout)

from core.open_with import OpenWith, display_name
from ui.code_editor import CodeEditor
from ui.find_bar import wrap_with_find_bar
from ui.highlight import CodeHighlighter
from ui.layout_utils import flexible_label, fit_to_screen
from ui.text_wrap import wrap_state

MAX_CHARS = 1_000_000            # keep the UI responsive on huge files


def read_preview_text(path: str) -> Optional[str]:
    """Bound preview I/O; never load an enormous file just to show its head."""
    try:
        total = os.path.getsize(path)
        with open(path, "rb") as fh:
            data = fh.read(MAX_CHARS * 4 + 4)
        if b"\x00" in data[:8192]:
            return "[Binary file - no text preview available]"
        text = data.decode("utf-8", errors="replace")
        if total > len(data) or len(text) > MAX_CHARS:
            return (text[:MAX_CHARS] +
                    f"\n\n[... preview truncated (file size: {total:,} bytes) ...]")
        return text
    except OSError:
        return None


def render_version_text(text: Optional[str]) -> str:
    """Text shown for one version (handles missing / binary / huge files)."""
    if text is None:
        return "[This file does not exist in this version]"
    if "\x00" in text[:8192]:
        return "[Binary file - no text preview available]"
    if len(text) > MAX_CHARS:
        return (text[:MAX_CHARS]
                + f"\n\n[... truncated: showing the first {MAX_CHARS:,} of "
                  f"{len(text):,} characters ...]")
    return text


class FileViewDialog(QDialog):
    def __init__(self, rel: str, versions: List[Tuple[str, Optional[str]]],
                 disk_path: Optional[str] = None, parent=None,
                 opener: Optional[OpenWith] = None):
        super().__init__(parent)
        self.opener = opener or OpenWith()
        self.rel = rel
        self.versions = versions or [("File", None)]
        self.disk_path = disk_path
        self.setWindowTitle(f"View — {rel}")
        fit_to_screen(self, 900, 640)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.lbl_path = flexible_label(QLabel(rel), selectable=True)
        self.lbl_path.setToolTip(rel)
        self.lbl_path.setTextFormat(Qt.PlainText)
        self.lbl_path.setStyleSheet("font-weight:bold;")
        top.addWidget(self.lbl_path, 1)
        self.combo = QComboBox()
        self.combo.setMinimumContentsLength(12)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.combo.addItems([label for label, _ in self.versions])
        self.combo.setVisible(len(self.versions) > 1)
        top.addWidget(self.combo)
        layout.addLayout(top)

        self.view = CodeEditor(read_only=True)         # line numbers + monospace
        self.highlighter = CodeHighlighter(self.view.document(), filename=rel)
        self.find_box = wrap_with_find_bar(self.view)  # Ctrl+F
        layout.addWidget(self.find_box, 1)

        bottom = QHBoxLayout()
        self.chk_wrap = QCheckBox("Wrap lines")
        self.chk_wrap.setToolTip("Wrap long lines instead of scrolling sideways (Alt+Z).\n"
                                 "Same setting as View ▸ Wrap Text.")
        self.chk_wrap.setChecked(wrap_state().enabled())
        bottom.addWidget(self.chk_wrap)
        self.lbl_info = flexible_label(QLabel(""))
        self.lbl_info.setForegroundRole(QPalette.PlaceholderText)      # readable in light AND dark themes
        bottom.addWidget(self.lbl_info, 1)
        can_open = bool(disk_path) and os.path.isfile(disk_path or "")
        self.btn_external = QPushButton("Open in Default App")
        self.btn_external.setToolTip(
            "Open the file on disk with the application your system associates with it")
        self.btn_external.setEnabled(can_open)
        # right next to it: pick any other program (remembered for next time)
        self.btn_open_with = QPushButton("Open with…")
        self.btn_open_with.setToolTip("Open the file on disk with a program you choose")
        self.btn_open_with.setEnabled(can_open)
        self.menu_open_with = QMenu(self.btn_open_with)
        self.menu_open_with.setToolTipsVisible(True)
        self.menu_open_with.aboutToShow.connect(self._fill_open_with_menu)
        self.btn_open_with.setMenu(self.menu_open_with)
        self.btn_close = QPushButton("Close")
        bottom.addWidget(self.btn_external)
        bottom.addWidget(self.btn_open_with)
        bottom.addWidget(self.btn_close)
        layout.addLayout(bottom)

        self.chk_wrap.toggled.connect(wrap_state().set_enabled)
        wrap_state().changed.connect(self._sync_wrap)
        QShortcut(QKeySequence("Alt+Z"), self).activated.connect(self.chk_wrap.toggle)
        self.combo.currentIndexChanged.connect(self._show_version)
        self.btn_external.clicked.connect(self._open_external)
        self.btn_close.clicked.connect(self.accept)
        self._show_version(0)

    def _show_version(self, index: int):
        index = max(0, min(index, len(self.versions) - 1))
        label, text = self.versions[index]
        self.view.setPlainText(render_version_text(text))
        if text is None:
            self.lbl_info.setText(label)
        else:
            self.lbl_info.setText(f"{label} · {text.count(chr(10)) + 1 if text else 0} lines "
                                  f"· {len(text):,} characters")

    def _sync_wrap(self, on: bool) -> None:
        self.chk_wrap.blockSignals(True)
        self.chk_wrap.setChecked(on)
        self.chk_wrap.blockSignals(False)

    def _open_external(self):
        if self.disk_path and os.path.isfile(self.disk_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.disk_path))

    # ------------------------------------------------------------ Open with…
    @staticmethod
    def _short(path: str, limit: int = 56) -> str:
        home = os.path.expanduser("~")
        shown = ("~" + path[len(home):]) if path.startswith(home) else path
        return shown if len(shown) <= limit else shown[:limit // 3] + " … " + shown[-(limit * 2 // 3):]

    def _fill_open_with_menu(self) -> None:
        menu = self.menu_open_with
        menu.clear()
        saved = self.opener.programs()
        for prog in saved:
            label = f"{prog['name']}    —    {self._short(prog['path'])}".replace("&", "&&")
            act = menu.addAction(label if prog["exists"] else f"{label}    (not found)")
            act.setToolTip(prog["path"])
            act.setEnabled(prog["exists"])
            act.triggered.connect(lambda _=False, p=prog["path"]: self._open_with(p))
        if saved:
            menu.addSeparator()
        menu.addAction("Choose program…").triggered.connect(self._choose_program)
        if saved:
            menu.addAction("Forget saved programs").triggered.connect(self.opener.clear)

    def _choose_program(self) -> None:
        if sys.platform == "win32":
            start, flt = os.environ.get("ProgramFiles", ""), "Programs (*.exe *.com *.bat *.cmd);;All files (*)"
        elif sys.platform == "darwin":
            start, flt = "/Applications", "Applications (*.app);;All files (*)"
        else:
            start, flt = "/usr/bin", "All files (*)"
        program, _ = QFileDialog.getOpenFileName(
            self, f"Open {os.path.basename(self.disk_path or self.rel)} with…", start, flt)
        if program:
            self._open_with(program)

    def _open_with(self, program: str) -> None:
        ok, error = self.opener.launch(program, self.disk_path or "")
        if ok:
            self.lbl_info.setText(f"Opened with {display_name(program)}")
        else:
            QMessageBox.warning(self, "Open with…", error)
