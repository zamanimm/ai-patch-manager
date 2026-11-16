"""CodeEditor: a monospace QPlainTextEdit with a line-number margin.

The margin is painted by the editor itself, so it always stays in sync with
scrolling (clicking / dragging on a number selects whole lines).
"""
from __future__ import annotations

import os

from bisect import bisect_right
from typing import List, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ui.text_wrap import follow_wrap


def mono_font(point_size: int = 11) -> QFont:
    font = QFont("Consolas" if os.name == "nt" else "Monospace", point_size)
    font.setStyleHint(QFont.Monospace)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.paint_line_numbers(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            mods = event.modifiers()
            self._editor.gutter_press(int(event.position().y()),
                                      bool(mods & Qt.ShiftModifier),
                                      bool(mods & (Qt.ControlModifier | Qt.MetaModifier)))

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self._editor.gutter_drag(int(event.position().y()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._editor.gutter_release()


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None, *, read_only: bool = True, point_size: int = 11):
        super().__init__(parent)
        self.setFont(mono_font(point_size))
        follow_wrap(self)                      # View ▸ Wrap Text (on for new installations)
        self.setReadOnly(read_only)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self._gutter_anchor = 0
        self._marked: List[Tuple[int, int]] = []      # 1-based inclusive ranges shown in the margin
        self._base_sel: list = []                     # e.g. the picked line ranges
        self._find_sel: list = []                     # search highlights (FindBar)
        self._area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_area)
        self.cursorPositionChanged.connect(self._area.update)
        self._update_margin()

    # -- extra selections: several features share them -----------------------
    def set_base_selections(self, sels: list) -> None:
        self._base_sel = list(sels)
        self._apply_selections()

    def set_find_selections(self, sels: list) -> None:
        self._find_sel = list(sels)
        self._apply_selections()

    def _apply_selections(self) -> None:
        super().setExtraSelections(self._base_sel + self._find_sel)

    # -- marked line ranges (painted in the number margin) ---------------------
    def set_marked_ranges(self, ranges: List[Tuple[int, int]]) -> None:
        self._marked = sorted(ranges)
        self._area.update()

    def _is_marked(self, line_no: int) -> bool:
        """line_no is 1-based."""
        i = bisect_right(self._marked, (line_no, 10 ** 12)) - 1
        return i >= 0 and self._marked[i][0] <= line_no <= self._marked[i][1]

    # -- geometry --------------------------------------------------------
    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * max(3, digits)

    def _update_margin(self, *_):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._area.scroll(0, dy)
        else:
            self._area.update(0, rect.y(), self._area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    # -- painting --------------------------------------------------------
    def paint_line_numbers(self, event) -> None:
        pal = self.palette()
        base = pal.base().color()
        dark = base.lightness() < 128
        painter = QPainter(self._area)
        painter.fillRect(event.rect(), base.lighter(125) if dark else base.darker(104))
        painter.setPen(QColor("#6e7681" if dark else "#8c959f"))
        current = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()
        font = painter.font()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if self._marked and self._is_marked(number + 1):
                    accent = QColor(pal.highlight().color())
                    tint = QColor(accent)
                    tint.setAlpha(90)
                    painter.fillRect(0, top, self._area.width(), round(self.blockBoundingRect(block).height()), tint)
                    painter.fillRect(0, top, 3, round(self.blockBoundingRect(block).height()), accent)
                font.setBold(number == current)
                painter.setFont(font)
                painter.setPen(QColor("#c9d1d9" if dark else "#24292f") if number == current
                               else QColor("#6e7681" if dark else "#8c959f"))
                painter.drawText(0, top, self._area.width() - 8, height, Qt.AlignRight, str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    # -- click / drag on the numbers selects whole lines ---------------------
    def _block_at(self, y: int):
        return self.cursorForPosition(QPoint(0, max(0, y))).block()

    def gutter_press(self, y: int, extend: bool, toggle: bool = False) -> None:
        block = self._block_at(y)
        cur = self.textCursor()
        if extend and cur.hasSelection():
            anchor_block = self.document().findBlock(self._gutter_anchor)
        else:
            anchor_block = block
            self._gutter_anchor = block.position()
        self._select_blocks(anchor_block, block)

    def gutter_drag(self, y: int) -> None:
        self._select_blocks(self.document().findBlock(self._gutter_anchor), self._block_at(y))

    def gutter_release(self) -> None:
        """Mouse released on the number margin (hook for subclasses)."""

    def _select_blocks(self, a, b) -> None:
        first, last = (a, b) if a.blockNumber() <= b.blockNumber() else (b, a)
        cur = QTextCursor(first)
        cur.setPosition(first.position())
        cur.setPosition(last.position() + max(0, last.length() - 1), QTextCursor.KeepAnchor)
        self.setTextCursor(cur)
