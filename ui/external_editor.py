"""Launch the chosen external code editor, never execute .py via file association."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QMessageBox

from core.open_with import OpenWith


EDITOR_KEY = "preferred_code_editor"


def open_in_code_editor(file_path: str, parent=None, *, force_choose: bool = False) -> bool:
    """Choose a code editor on first use; reuse it on subsequent double-clicks."""
    if not os.path.isfile(file_path):
        QMessageBox.warning(parent, "Open in editor", f"File does not exist on disk:\n{file_path}")
        return False
    settings = QSettings("AIPatchManager", "AIPatchManager")
    program = "" if force_choose else (settings.value(EDITOR_KEY, "", type=str) or "")
    if not program or OpenWith.check_program(program):
        if sys.platform == "win32":
            start = os.environ.get("ProgramFiles", "")
            flt = "Editor programs (*.exe *.com);;All files (*)"
        elif sys.platform == "darwin":
            start, flt = "/Applications", "Applications (*.app);;All files (*)"
        else:
            start, flt = "/usr/bin", "All files (*)"
        program, _ = QFileDialog.getOpenFileName(
            parent, "Choose a code editor executable (not the project file)", start, flt)
        if not program:
            return False
    opener = OpenWith()
    ok, error = opener.launch(program, file_path)
    if not ok:
        QMessageBox.warning(parent, "Open in editor", error)
        return False
    settings.setValue(EDITOR_KEY, program)
    return True
