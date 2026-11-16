"""Dialog for picking line ranges of a file (selection / context modes).

Several separate ranges can be picked; each one is kept as it is (they are
NOT merged into one span) and shown as a persistent highlight, in the number
margin and in the "Selected ranges" list on the right.

    click / drag the line numbers        select those lines (replaces the previous pick)
    Ctrl + click / drag the line numbers  add lines  (on an already picked line: remove them)
    Shift + click a line number           extend from the last clicked line
    drag over the code                    select lines   (Ctrl + drag: add them)
    Ctrl + click on the code              toggle that one line
    Ctrl + A                              all lines

The chosen ranges are returned as inclusive 1-based (start, end) tuples and persisted
per workspace by the controller.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from qtpy.QtCore import QTimer, Qt, Signal
from qtpy.QtGui import QColor, QKeySequence, QPalette, QShortcut, QTextCursor, QTextFormat
from qtpy.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter,
                               QTextEdit, QVBoxLayout, QWidget)

from ui.code_editor import CodeEditor
from ui.find_bar import wrap_with_find_bar
from ui.highlight import CodeHighlighter
from ui.layout_utils import flexible_label, fit_to_screen
from ui.text_wrap import wrap_state

Range = Tuple[int, int]


def merge_ranges(ranges: List[Range], join_adjacent: bool = False) -> List[Range]:
    """Sort and merge overlapping inclusive ranges.

    Adjacent ranges (5-10 and 11-20) stay separate unless join_adjacent=True."""
    cleaned = sorted({(max(1, s), max(s, e)) for s, e in ranges if e >= s})
    merged: List[Range] = []
    gap = 1 if join_adjacent else 0
    for s, e in cleaned:
        if merged and s <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _subtract(ranges: List[Range], s: int, e: int) -> List[Range]:
    out: List[Range] = []
    for a, b in ranges:
        if b < s or a > e:
            out.append((a, b))
            continue
        if a < s:
            out.append((a, s - 1))
        if b > e:
            out.append((e + 1, b))
    return out


class _LineSelectTextEdit(CodeEditor):
    """Read-only code view where whole lines are picked (any number of ranges)."""

    rangesChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, read_only=True)
        self._ranges: List[Range] = []
        self._press_ctrl = False
        self._mode: Optional[str] = None          # while dragging on the numbers: replace | add | remove
        self._anchor: Optional[int] = None        # last clicked line (Shift+click extends from it)
        self._base: List[Range] = []              # ranges as they were when the drag started

    # -- model -----------------------------------------------------------------
    def line_count(self) -> int:
        doc = self.document()
        n = doc.blockCount()
        if n > 1 and doc.lastBlock().length() <= 1:      # file ends with a newline: no phantom line
            n -= 1
        return 0 if (n == 1 and doc.firstBlock().length() <= 1) else n

    def selected_line_ranges(self) -> List[Range]:
        return list(self._ranges)

    def set_selected_ranges(self, ranges: List[Range], scroll: bool = True) -> None:
        total = self.line_count()
        clamped = [(max(1, s), min(e, total)) for s, e in ranges if total and e >= 1 and s <= total]
        self._ranges = merge_ranges(clamped, join_adjacent=True)
        self._refresh()
        if scroll and self._ranges:
            self.scroll_to_line(self._ranges[0][0])

    def clear_selection_ranges(self) -> None:
        self._ranges = []
        self._refresh()

    def select_all_lines(self) -> None:
        self.set_selected_ranges([(1, self.line_count())] if self.line_count() else [], scroll=False)

    def remove_range(self, s: int, e: int) -> None:
        self._ranges = _subtract(self._ranges, s, e)
        self._refresh()

    def scroll_to_line(self, line: int) -> None:
        """Bring *line* into view (centred). Done through the cursor on purpose: the scroll bar counts
        VISUAL lines, and a plain text edit lays out lazily, so line positions are unreliable right
        after wrapping was switched on or off - centerCursor() forces the layout it needs."""
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            self.setTextCursor(QTextCursor(block))
            self.centerCursor()

    def _in_ranges(self, line: int) -> bool:
        return any(a <= line <= b for a, b in self._ranges)

    # -- painting ----------------------------------------------------------------
    def _refresh(self) -> None:
        doc = self.document()
        tint = QColor(self.palette().highlight().color())
        tint.setAlpha(75)
        sels = []
        for s, e in self._ranges:
            first, last = doc.findBlockByNumber(s - 1), doc.findBlockByNumber(e - 1)
            if not (first.isValid() and last.isValid()):
                continue
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(tint)
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            cur = QTextCursor(doc)
            cur.setPosition(first.position())
            cur.setPosition(last.position() + max(0, last.length() - 1), QTextCursor.KeepAnchor)
            sel.cursor = cur
            sels.append(sel)
        self.set_base_selections(sels)
        self.set_marked_ranges(self._ranges)
        self.rangesChanged.emit()

    # -- mouse on the code -------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        self._press_ctrl = bool(event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.LeftButton:
            return
        cur = self.textCursor()
        if cur.hasSelection():
            s, e = self._lines_of(cur)
            self._ranges = (merge_ranges(self._ranges + [(s, e)], join_adjacent=True)
                            if self._press_ctrl else [(s, e)])
            self._anchor = s
            cur.clearSelection()                    # the highlight now lives in the ranges
            self.setTextCursor(cur)
            self._refresh()
        elif self._press_ctrl:                      # Ctrl + plain click: toggle that line
            line = cur.blockNumber() + 1
            self._ranges = (_subtract(self._ranges, line, line) if self._in_ranges(line)
                            else merge_ranges(self._ranges + [(line, line)], join_adjacent=True))
            self._anchor = line
            self._refresh()

    @staticmethod
    def _lines_of(cur: QTextCursor) -> Range:
        doc = cur.document()
        a, b = cur.selectionStart(), cur.selectionEnd()
        first = doc.findBlock(a).blockNumber() + 1
        end_block = doc.findBlock(b)
        if b > a and end_block.position() == b:      # selection ends at the start of a line: that line is not in
            end_block = end_block.previous()
        return first, max(first, end_block.blockNumber() + 1)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.SelectAll):
            self.select_all_lines()
            return
        super().keyPressEvent(event)

    # -- mouse on the line numbers ---------------------------------------------------
    def _line_at_y(self, y: int) -> int:
        return self._block_at(y).blockNumber() + 1

    def gutter_press(self, y: int, extend: bool, toggle: bool = False) -> None:
        line = min(self._line_at_y(y), max(1, self.line_count()))
        if toggle:                                   # Ctrl: add, or remove when already picked
            self._mode = "remove" if self._in_ranges(line) else "add"
            self._base = list(self._ranges)
            self._anchor = line
        elif extend and self._anchor is not None:    # Shift: from the last clicked line
            self._mode, self._base = "replace", []
        else:                                        # plain: pick just this line (drag to widen)
            self._mode, self._base = "replace", []
            self._anchor = line
        self._apply_drag(line)

    def gutter_drag(self, y: int) -> None:
        if self._mode:
            self._apply_drag(min(self._line_at_y(y), max(1, self.line_count())))

    def gutter_release(self) -> None:
        self._mode = None

    def _apply_drag(self, line: int) -> None:
        self.setTextCursor(QTextCursor(self.document().findBlockByNumber(line - 1)))   # caret follows the mouse
        a = self._anchor if self._anchor is not None else line
        s, e = min(a, line), max(a, line)
        if self._mode == "add":
            self._ranges = merge_ranges(self._base + [(s, e)], join_adjacent=True)
        elif self._mode == "remove":
            self._ranges = _subtract(self._base, s, e)
        else:
            self._ranges = [(s, e)]
        self._refresh()


class CodeSelectDialog(QDialog):
    """Modal dialog: pick line ranges in a file for prompt inclusion."""

    def __init__(self, abs_path: str, rel_path: str,
                 initial: Optional[List[Range]] = None,
                 parent: QWidget = None):
        super().__init__(parent)
        self.abs_path = abs_path
        self.rel_path = rel_path
        self.setWindowTitle(f"Select lines — {rel_path}")
        fit_to_screen(self, 1150, 700)

        text = self._read_file()
        self.total_lines = len(text.splitlines())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # header: colours come from the palette, so it reads well in light AND dark themes
        title = flexible_label(QLabel(f"{rel_path}  ·  {self.total_lines} lines"), selectable=True)
        title.setTextFormat(Qt.PlainText)
        title.setToolTip(rel_path)
        title.setStyleSheet("font-size:12pt; font-weight:bold;")
        hint = QLabel(
            "Click or drag the line numbers to select lines   ·   Ctrl = add / remove   ·   "
            "Shift = extend   ·   or drag over the code   ·   Ctrl+F searches")
        flexible_label(hint)
        hint.setForegroundRole(QPalette.PlaceholderText)
        layout.addWidget(title)
        layout.addWidget(hint)

        # code view: line numbers are painted by the editor itself (always in sync)
        self.editor = _LineSelectTextEdit()
        self.editor.setPlainText(text)
        self.highlighter = CodeHighlighter(self.editor.document(), filename=rel_path)
        self.find_box = wrap_with_find_bar(self.editor)          # Ctrl+F

        # right: the picked ranges, each one kept separately
        side = QWidget()
        side_l = QVBoxLayout(side)
        side_l.setContentsMargins(6, 0, 0, 0)
        side_l.addWidget(QLabel("<b>Selected ranges</b>"))
        self.range_list = QListWidget()
        self.range_list.setToolTip("Double-click a range to jump to it · Delete removes it")
        side_l.addWidget(self.range_list, 1)
        self.btn_remove = QPushButton("Remove range")
        side_l.addWidget(self.btn_remove)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.find_box)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([900, 230])
        layout.addWidget(splitter, 1)

        # bottom bar: buttons on the LEFT (user preference)
        bottom = QHBoxLayout()
        self.chk_wrap = QCheckBox("Wrap lines")
        self.chk_wrap.setToolTip("Wrap long lines instead of scrolling sideways (Alt+Z).\n"
                                 "Same setting as View ▸ Wrap Text.")
        self.chk_wrap.setChecked(wrap_state().enabled())
        bottom.addWidget(self.chk_wrap)
        self.btn_all = QPushButton("Select All Lines")
        self.btn_clear = QPushButton("Clear Selection")
        self.lbl_state = flexible_label(QLabel(""))
        bottom.addWidget(self.btn_all)
        bottom.addWidget(self.btn_clear)
        bottom.addWidget(self.lbl_state, 1)
        bottom.addStretch(1)
        self.btn_ok = QPushButton("Save Ranges")
        self.btn_cancel = QPushButton("Cancel")
        bottom.addWidget(self.btn_ok)
        bottom.addWidget(self.btn_cancel)
        layout.addLayout(bottom)

        self.chk_wrap.toggled.connect(wrap_state().set_enabled)
        wrap_state().changed.connect(self._on_wrap_changed)
        QShortcut(QKeySequence("Alt+Z"), self).activated.connect(self.chk_wrap.toggle)
        self.btn_all.clicked.connect(self.editor.select_all_lines)
        self.btn_clear.clicked.connect(self.editor.clear_selection_ranges)
        self.btn_remove.clicked.connect(self._remove_current)
        self.range_list.itemDoubleClicked.connect(self._jump_to)
        sc_del = QShortcut(QKeySequence(Qt.Key_Delete), self.range_list)
        sc_del.setContext(Qt.WidgetShortcut)
        sc_del.activated.connect(self._remove_current)
        self.editor.rangesChanged.connect(self._update_state)
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

    def _update_state(self) -> None:
        ranges = self.editor.selected_line_ranges()
        count = sum(e - s + 1 for s, e in ranges)
        self.range_list.clear()
        for s, e in ranges:
            n = e - s + 1
            label = f"{s}" if s == e else f"{s} – {e}"
            item = QListWidgetItem(f"{label}    ({n} line{'s' if n != 1 else ''})")
            item.setData(Qt.UserRole, (s, e))
            self.range_list.addItem(item)
        self.btn_remove.setEnabled(bool(ranges))
        if ranges:
            pretty = ", ".join(f"{s}-{e}" if s != e else f"{s}" for s, e in ranges[:8])
            if len(ranges) > 8:
                pretty += f" ... (+{len(ranges) - 8} more)"
            self.lbl_state.setText(
                f"<span style='color:#0a7'>Selected: {pretty} "
                f"({count} lines in {len(ranges)} range{'s' if len(ranges) != 1 else ''})</span>")
        else:
            self.lbl_state.setText(
                "<span style='color:#a60'>No lines selected — "
                "saving will clear this file's ranges</span>")

    def _on_wrap_changed(self, on: bool) -> None:
        self.chk_wrap.blockSignals(True)
        self.chk_wrap.setChecked(on)
        self.chk_wrap.blockSignals(False)
        QTimer.singleShot(0, self._scroll_to_first_range)     # re-layout changes the line positions

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._scroll_to_first_range)     # the layout only exists once shown

    def _scroll_to_first_range(self) -> None:
        try:                                                   # the dialog may be gone by the time the timer fires
            ranges = self.editor.selected_line_ranges()
            if ranges:
                self.editor.scroll_to_line(ranges[0][0])
        except RuntimeError:
            pass

    def _jump_to(self, item: QListWidgetItem) -> None:
        s, _e = item.data(Qt.UserRole)
        self.editor.scroll_to_line(s)

    def _remove_current(self) -> None:
        item = self.range_list.currentItem()
        if item is not None:
            s, e = item.data(Qt.UserRole)
            self.editor.remove_range(s, e)

    def _accept(self) -> None:
        self.ranges = self.editor.selected_line_ranges()
        self.accept()

    # public ------------------------------------------------------------
    def selected_ranges(self) -> List[Range]:
        return getattr(self, "ranges", [])
