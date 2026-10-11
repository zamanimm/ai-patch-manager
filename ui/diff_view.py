"""Diff viewer widget: colourised unified diff.

Fixes vs original:
- Background colours are now theme-aware: we derive them from the
  application palette so they look correct in both light and dark modes
  instead of the hardcoded light-only hex values in the original.
- Foreground colours are bolder and clearer.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QPlainTextEdit


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
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setPlaceholderText("No diff selected.")
        # Better margins and padding
        self.setContentsMargins(5, 5, 5, 5)

    def show_diff(self, text: str) -> None:
        self.clear()
        dark = _is_dark_mode()
        cursor = self.textCursor()
        fmt = QTextCharFormat()
        
        for line in text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                # File headers - subtle gray
                fmt.setForeground(QColor("#999999" if dark else "#666666"))
                fmt.setBackground(QColor("transparent"))
                fmt.setFontWeight(QFont.Bold)
            elif line.startswith("@@"):
                # Line numbers - blue/cyan for visibility
                fmt.setForeground(QColor("#66d9ff" if dark else "#0066cc"))
                fmt.setBackground(QColor("transparent"))
                fmt.setFontWeight(QFont.Bold)
            elif line.startswith("+"):
                # Added lines - vivid green
                if dark:
                    fmt.setForeground(QColor("#2eff2e"))  # Bright green
                    fmt.setBackground(QColor("#1a3a1a"))  # Dark green background
                else:
                    fmt.setForeground(QColor("#0a7a3d"))  # Dark green text
                    fmt.setBackground(QColor("#c8f0d8"))  # Light green background
                fmt.setFontWeight(QFont.Normal)
            elif line.startswith("-"):
                # Removed lines - vivid red
                if dark:
                    fmt.setForeground(QColor("#ff6b6b"))  # Bright red
                    fmt.setBackground(QColor("#3d1a1a"))  # Dark red background
                else:
                    fmt.setForeground(QColor("#b4232a"))  # Dark red text
                    fmt.setBackground(QColor("#f8d7da"))  # Light red background
                fmt.setFontWeight(QFont.Normal)
            else:
                # Context lines - neutral
                fmt.setForeground(QColor("#cccccc" if dark else "#333333"))
                fmt.setBackground(QColor("transparent"))
                fmt.setFontWeight(QFont.Normal)
            
            cursor.insertText(line + "\n", fmt)


def _os_is_windows() -> bool:
    import os as _os
    return _os.name == "nt"
