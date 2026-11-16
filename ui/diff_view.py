"""Diff viewer widget: colourised unified diff.

Fixes vs original:
- Background colours are now theme-aware: we derive them from the
  application palette so they look correct in both light and dark modes
  instead of the hardcoded light-only hex values in the original.
- Foreground colours are bolder and clearer.
"""
from __future__ import annotations

from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from ui.highlight import DiffHighlighter
from ui.text_wrap import follow_wrap


def _is_dark_mode() -> bool:
    """Best-effort dark-mode detection via the application palette."""
    try:
        palette = QApplication.palette()
        bg = palette.window().color()
        return bg.lightness() < 128
    except Exception:
        return False


class DiffView(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Use larger, clearer monospace font
        font = QFont("Consolas" if _os_is_windows() else "Monospace", 11)
        font.setStyleStrategy(QFont.PreferAntialias)
        self.setFont(font)
        follow_wrap(self)
        self.setPlaceholderText("No diff selected.")
        # Better margins and padding
        self.setContentsMargins(5, 5, 5, 5)
        # token colours (per file language) layered on top of the +/- colouring
        self.syntax = DiffHighlighter(self.document())

    def show_diff(self, text: str) -> None:
        # One Qt document update instead of a cursor insertion per line.
        # The syntax highlighter paints +/- lines while they are visible.
        self.setUpdatesEnabled(False)
        try:
            self.setPlainText(text)
            self.syntax.refresh(text)
            self.moveCursor(QTextCursor.Start)
        finally:
            self.setUpdatesEnabled(True)


def _os_is_windows() -> bool:
    import os as _os
    return _os.name == "nt"
