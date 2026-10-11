"""Code viewer dialog for picking line ranges (selection / context modes).

Opens a read-only view of a file with 1-based line numbers. The user drags
(or uses Shift+click) to select one or more blocks of lines; the chosen
ranges are returned as a list of inclusive 1-based (start, end) tuples and
persisted per workspace by the controller.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSplitter, QTextEdit,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)


class _LineSelectTextEdit(QTextEdit):
    """Read-only text edit that highlights whole lines on selection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.NoWrap)
        self._sel_color = QColor("#d5e8ff")
        try:
            self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        except AttributeError:  # older Qt
            self.setTabStopWidth(4 * self.fontMetrics().width(" "))
        self.selectionChanged.connect(self._repaint_selection)

    # -- helpers ---------------------------------------------------------
    def _line_at_pos(self, pos: int) -> int:
        return self.document().findBlock(pos).blockNumber() + 1

    def selected_line_ranges(self) -> List[Tuple[int, int]]:
        """Return the inclusive 1-based line range of the current selection."""
        cur = self.textCursor()
        if not cur.hasSelection():
            return []
        start = self._line_at_pos(cur.selectionStart())
        end = self._line_at_pos(cur.selectionEnd())
        if start > end:
            start, end = end, start
        return merge_ranges([(start, end)])

    def set_selected_ranges(self, ranges: List[Tuple[int, int]]) -> None:
        """Apply (select) the given 1-based inclusive ranges.

        QTextEdit only supports a single selection, so all ranges are merged
        into one contiguous span; exact per-range info is kept by the caller
        (controller / prompt builder).
        """
        self.clear_selection_ranges()
        ranges = merge_ranges(ranges)
        if not ranges:
            return
        doc = self.document()
        first = doc.findBlockByLineNumber(max(0, ranges[0][0] - 1))
        last = doc.findBlockByLineNumber(max(0, ranges[-1][1] - 1))
        cur = QTextCursor(first)
        cur.setPosition(first.position())
        cur.setPosition(last.position() + max(0, last.length() - 1),
                        QTextCursor.KeepAnchor)
        self.setTextCursor(cur)
        # scroll the first range into view
        self.verticalScrollBar().setValue(
            min(self.verticalScrollBar().value(),
                self.verticalScrollBar().maximum()))

    def clear_selection_ranges(self) -> None:
        cur = self.textCursor()
        cur.setPosition(cur.position())
        self.setTextCursor(cur)

    def _repaint_selection(self):
        # force viewport update so highlight stays in sync
        self.viewport().update()


def merge_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Sort and merge overlapping inclusive ranges (adjacent stays separate)."""
    cleaned = sorted({(max(1, s), max(s, e)) for s, e in ranges if e >= s})
    merged: List[Tuple[int, int]] = []
    for s, e in cleaned:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


class CodeSelectDialog(QDialog):
    """Modal dialog: pick line ranges in a file for prompt inclusion."""

    def __init__(self, abs_path: str, rel_path: str,
                 initial: Optional[List[Tuple[int, int]]] = None,
                 parent: QWidget = None):
        super().__init__(parent)
        self.abs_path = abs_path
        self.rel_path = rel_path
        self.setWindowTitle(f"Select lines — {rel_path}")
        self.resize(1100, 700)

        text = self._read_file()
        self.total_lines = text.count("\n") + 1 if text else 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # Header with file info and instructions
        info = QLabel(
            f"<b style='font-size:12pt'>{rel_path}</b><br>"
            f"<span style='color:#666; font-size:10pt'>"
            f"{self.total_lines} lines • Drag to select ranges • Selection mode = exact lines • Context mode = padded area"
            f"</span>")
        info.setTextFormat(Qt.RichText)
        info.setStyleSheet("padding: 8px; background-color: #f5f5f5; border-radius: 4px;")
        layout.addWidget(info)

        splitter = QSplitter(Qt.Horizontal)

        # left: gutter with line numbers (kept in sync with the editor)
        self.gutter = QTreeWidget()
        self.gutter.setHeaderHidden(True)
        self.gutter.setColumnCount(1)
        self.gutter.setRootIsDecorated(False)
        self.gutter.setMaximumWidth(90)
        hdr = self.gutter.header()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        items = [QTreeWidgetItem([str(i)]) for i in range(1, self.total_lines + 1)]
        self.gutter.addTopLevelItems(items)
        for it in items:
            f = it.font(0)
            f.setFamily("monospace")
            it.setFont(0, f)
            it.setForeground(0, QColor("#888"))
            it.setTextAlignment(0, Qt.AlignRight | Qt.AlignVCenter)
            it.setFlags(Qt.NoItemFlags)

        self.editor = _LineSelectTextEdit()
        mono = self.editor.font()
        mono.setFamily("Consolas" if os.name == "nt" else "Monospace")
        mono.setStyleHint(getattr(__import__("PySide6.QtGui", fromlist=["QFont"]),
                                  "QFont").Monospace)
        self.editor.setFont(mono)
        self.editor.setHtml(self._to_html(text))

        splitter.addWidget(self.gutter)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        # bottom bar: buttons on the LEFT (user preference)
        bottom = QHBoxLayout()
        self.btn_all = QPushButton("Select All Lines")
        self.btn_clear = QPushButton("Clear Selection")
        self.lbl_state = QLabel("")
        bottom.addWidget(self.btn_all)
        bottom.addWidget(self.btn_clear)
        bottom.addWidget(self.lbl_state, 1)
        bottom.addStretch(1)
        self.btn_ok = QPushButton("Save Ranges")
        self.btn_cancel = QPushButton("Cancel")
        bottom.addWidget(self.btn_ok)
        bottom.addWidget(self.btn_cancel)
        layout.addLayout(bottom)

        self.btn_all.clicked.connect(lambda: self.editor.set_selected_ranges(
            [(1, self.total_lines)] if self.total_lines else []))
        self.btn_clear.clicked.connect(self.editor.clear_selection_ranges)
        self.editor.selectionChanged.connect(self._update_state)
        self.btn_ok.clicked.connect(self._accept)
        self.btn_cancel.clicked.connect(self.reject)

        if initial:
            self.editor.set_selected_ranges(merge_ranges(initial))
        self._update_state()

    # ------------------------------------------------------------------
    def _read_file(self) -> str:
        try:
            with open(self.abs_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    @staticmethod
    def _to_html(text: str) -> str:
        """Convert code to HTML with basic syntax highlighting."""
        import html
        import re
        
        # Keywords, strings, comments coloring
        def highlight_line(line: str) -> str:
            escaped = html.escape(line)
            
            # Comments (Python #, JavaScript //, etc.)
            escaped = re.sub(
                r'(#.*?)$|(//.*)$',
                r'<span style="color:#008000">\1\2</span>',  # Green
                escaped)
            
            # Strings (double and single quotes)
            escaped = re.sub(
                r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')',
                r'<span style="color:#cc0000">\1</span>',  # Red
                escaped)
            
            # Keywords (Python, JS, etc.)
            keywords = r'\b(def|class|import|from|if|else|elif|for|while|try|except|return|True|False|None|async|await|function|const|let|var|if|else|return|class|extends|interface|public|private|protected)\b'
            escaped = re.sub(
                keywords,
                r'<span style="color:#0066cc; font-weight:bold">\1</span>',  # Blue bold
                escaped)
            
            # Numbers
            escaped = re.sub(
                r'\b(\d+\.?\d*)\b',
                r'<span style="color:#ff6600">\1</span>',  # Orange
                escaped)
            
            return escaped or '&nbsp;'
        
        lines = text.splitlines()
        rows = "".join(
            f'<div style="padding:2px 4px; white-space:pre; background-color:#fafafa">'
            f'{highlight_line(l)}</div>'
            for l in lines)
        
        return (f'<div style="font-family:\'Consolas\', \'Courier New\', monospace; '
                f'font-size:11px; line-height:1.5; background-color:#fff; padding:8px">'
                f'{rows}</div>')

    def _update_state(self):
        ranges = self.editor.selected_line_ranges()
        count = sum(e - s + 1 for s, e in ranges)
        if ranges:
            pretty = ", ".join(f"{s}-{e}" for s, e in ranges[:8])
            if len(ranges) > 8:
                pretty += f" ... (+{len(ranges) - 8} more)"
            self.lbl_state.setText(
                f"<span style='color:#0a7'>Selected: {pretty} "
                f"({count} lines)</span>")
        else:
            self.lbl_state.setText(
                "<span style='color:#a60'>No lines selected — "
                "saving will clear this file's ranges</span>")

    def _accept(self):
        self.ranges = self.editor.selected_line_ranges()
        self.accept()

    # public ------------------------------------------------------------
    def selected_ranges(self) -> List[Tuple[int, int]]:
        return getattr(self, "ranges", [])
