"""Find (and optionally replace) bar for the text / code viewers.

    box = wrap_with_find_bar(editor)                          # search only
    box = wrap_with_find_bar(editor, allow_replace=True)      # + replace in the editor's own text
    box = wrap_with_find_bar(editor, patch_replacer=obj)      # + replace inside the parsed patches

Keys (while the viewer has focus):  Ctrl+F find · F3 / Shift+F3 next / previous ·
Ctrl+H replace · Esc close.   Toggles:  Aa = match case · W = whole word · .* = regular expression.

Replace exists only where the text is meant to be edited (Response box, parsed patches). Everywhere else
the viewers show text that must stay exact (files, prompt, instructions, history), so they are search-only.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

MAX_MATCHES = 20_000        # counted / navigable
MAX_HIGHLIGHTS = 2_000      # painted in the editor


class _FindEdit(QLineEdit):
    nextRequested = Signal()
    prevRequested = Signal()
    closeRequested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            (self.prevRequested if event.modifiers() & Qt.ShiftModifier else self.nextRequested).emit()
            return
        if event.key() == Qt.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)


class _Offsets:
    """Python str index <-> Qt (UTF-16) document position."""

    def __init__(self, text: str):
        self._offs: Optional[List[int]] = None
        if any(ord(c) > 0xFFFF for c in text):
            offs = [0]
            for ch in text:
                offs.append(offs[-1] + (2 if ord(ch) > 0xFFFF else 1))
            self._offs = offs

    def qt(self, i: int) -> int:
        return i if self._offs is None else self._offs[i]

    def py(self, pos: int) -> int:
        return pos if self._offs is None else bisect_left(self._offs, pos)


def sub_nonempty(pattern: "re.Pattern", fn, text: str) -> Tuple[str, int]:
    """pattern.sub that only touches real (non-empty) matches - the same ones the
    bar counts and highlights. Returns (new_text, replacements)."""
    count = 0

    def _one(m):
        nonlocal count
        if m.end() == m.start():
            return m.group(0)
        count += 1
        return fn(m)

    return pattern.sub(_one, text), count


def _tool(text: str, tip: str, checkable: bool = True) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setAutoRaise(False)
    return b


class FindBar(QWidget):
    visibilityChanged = Signal(bool)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.visibilityChanged.emit(False)

    def __init__(self, editor, *, allow_replace: bool = False, patch_replacer=None, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.patch_replacer = patch_replacer
        self.can_replace_text = bool(allow_replace) and not editor.isReadOnly()
        self.can_replace = self.can_replace_text or patch_replacer is not None
        self._matches: List[Tuple[int, int]] = []
        self._error = ""
        self._note = ""
        self._pending_jump = False
        self._cached_text = None
        self._cached_offsets = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 2)
        root.setSpacing(2)

        # ---- row 1: find --------------------------------------------------
        row = QHBoxLayout()
        row.addWidget(QLabel("Find:"))
        self.find_edit = _FindEdit()
        self.find_edit.setPlaceholderText("Search…")
        self.find_edit.setClearButtonEnabled(True)
        row.addWidget(self.find_edit, 1)
        self.btn_case = _tool("Aa", "Match case")
        self.btn_word = _tool("W", "Whole word")
        self.btn_regex = _tool(".*", "Regular expression")
        self.btn_prev = _tool("▲", "Previous match (Shift+Enter / Shift+F3)", checkable=False)
        self.btn_next = _tool("▼", "Next match (Enter / F3)", checkable=False)
        for b in (self.btn_case, self.btn_word, self.btn_regex, self.btn_prev, self.btn_next):
            row.addWidget(b)
        self.lbl_status = QLabel("")
        self.lbl_status.setMinimumWidth(150)
        row.addWidget(self.lbl_status)
        self.btn_close = _tool("✕", "Close (Esc)", checkable=False)
        row.addWidget(self.btn_close)
        root.addLayout(row)

        # ---- row 2: replace (only where it makes sense) -----------------------
        self.replace_row = QWidget()
        rrow = QHBoxLayout(self.replace_row)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.addWidget(QLabel("Replace:"))
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Replace with…  (regex mode: \\1 \\g<name> allowed)")
        rrow.addWidget(self.replace_edit, 1)
        self.btn_replace = QPushButton("Replace")
        self.btn_replace_all = QPushButton("Replace All")
        self.scope_combo = QComboBox()
        self.btn_patch_replace = QPushButton("Replace in Patches")
        self.btn_patch_undo = QPushButton("Undo")
        if self.can_replace_text:
            rrow.addWidget(self.btn_replace)
            rrow.addWidget(self.btn_replace_all)
        if patch_replacer is not None:
            for key, label in patch_replacer.scopes:
                self.scope_combo.addItem(label, key)
            self.btn_patch_replace.setToolTip(
                "Changes only the PROPOSED code of the patches. The original file lines and the\n"
                "target anchors are never touched.")
            self.btn_patch_undo.setEnabled(False)
            rrow.addWidget(self.scope_combo)
            rrow.addWidget(self.btn_patch_replace)
            rrow.addWidget(self.btn_patch_undo)
        self.replace_row.setVisible(False)
        root.addWidget(self.replace_row)

        # ---- wiring ---------------------------------------------------------
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(200)
        self._refresh_timer.timeout.connect(self._refresh_debounced)
        self.find_edit.textChanged.connect(lambda *_: self._schedule_refresh(jump=True))
        for b in (self.btn_case, self.btn_word, self.btn_regex):
            b.toggled.connect(lambda *_: self._schedule_refresh(jump=True))
        self.find_edit.nextRequested.connect(self.find_next)
        self.find_edit.prevRequested.connect(self.find_prev)
        self.find_edit.closeRequested.connect(self.close_bar)
        self.btn_next.clicked.connect(self.find_next)
        self.btn_prev.clicked.connect(self.find_prev)
        self.btn_close.clicked.connect(self.close_bar)
        self.btn_replace.clicked.connect(self.replace_current)
        self.btn_replace_all.clicked.connect(self.replace_all)
        self.btn_patch_replace.clicked.connect(self.replace_in_patches)
        self.btn_patch_undo.clicked.connect(self.undo_patch_replace)
        self.replace_edit.returnPressed.connect(
            self.replace_in_patches if patch_replacer is not None else self.replace_current)
        editor.document().contentsChanged.connect(self._on_doc_changed)

    # ------------------------------------------------------------ open / close
    def open(self, replace: bool = False) -> None:
        self.show()
        cur = self.editor.textCursor()
        if cur.hasSelection():
            sel = cur.selectedText()
            if sel and "\u2029" not in sel and len(sel) <= 200:
                self.find_edit.setText(sel)
        self.replace_row.setVisible(bool(replace and self.can_replace))
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self._refresh(jump=False)

    def close_bar(self) -> None:
        self._refresh_timer.stop()
        self._pending_jump = False
        self.hide()
        self._set_selections([])
        self.editor.setFocus()

    # ------------------------------------------------------------ matching
    def _compile(self) -> Optional["re.Pattern"]:
        text = self.find_edit.text()
        self._error = ""
        if not text:
            return None
        try:
            pat = text if self.btn_regex.isChecked() else re.escape(text)
            if self.btn_word.isChecked():
                pat = rf"(?<!\w)(?:{pat})(?!\w)"
            flags = re.MULTILINE | (0 if self.btn_case.isChecked() else re.IGNORECASE)
            return re.compile(pat, flags)
        except re.error as exc:
            self._error = f"Invalid regex: {exc}"
            return None

    def pattern(self) -> Optional["re.Pattern"]:
        return self._compile()

    @property
    def match_count(self) -> int:
        return len(self._matches)

    def _document_text(self) -> str:
        if self._cached_text is None:
            self._cached_text = self.editor.toPlainText()
        return self._cached_text

    def _offsets(self) -> _Offsets:
        if self._cached_offsets is None:
            self._cached_offsets = _Offsets(self._document_text())
        return self._cached_offsets

    def _schedule_refresh(self, jump: bool = False) -> None:
        # One search for a burst of typing, instead of scanning a large document
        # on *every* keystroke. Navigation forces an immediate refresh below.
        self._pending_jump = self._pending_jump or jump
        if self.isVisible():
            self._refresh_timer.start()

    def _refresh_debounced(self) -> None:
        jump = self._pending_jump
        self._pending_jump = False
        self._refresh(jump=jump)

    def _on_doc_changed(self) -> None:
        self._cached_text = None
        self._cached_offsets = None
        self._schedule_refresh()

    def _ensure_current_matches(self) -> None:
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
            self._pending_jump = False
            self._refresh(jump=False)

    def _refresh(self, jump: bool = False) -> None:
        self._refresh_timer.stop()
        self._pending_jump = False
        pat = self._compile()
        text = self._document_text()
        self._matches = []
        if pat is not None:
            for m in pat.finditer(text):
                if m.end() > m.start():
                    self._matches.append((m.start(), m.end()))
                    if len(self._matches) >= MAX_MATCHES:
                        break
        self._paint_highlights(text)
        self._update_status(text)
        if jump and self._matches:
            self._goto(forward=True, include_caret=True)

    def _set_selections(self, sels) -> None:
        if hasattr(self.editor, "set_find_selections"):      # CodeEditor: shares the slot with other features
            self.editor.set_find_selections(sels)
        else:
            self.editor.setExtraSelections(sels)

    def _paint_highlights(self, text: str) -> None:
        if not self._matches:
            self._set_selections([])
            return
        off = self._offsets()
        dark = self.editor.palette().base().color().lightness() < 128
        bg = QColor("#5a4a00" if dark else "#fff3a8")
        sels = []
        for s, e in self._matches[:MAX_HIGHLIGHTS]:
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(bg)
            cur = QTextCursor(self.editor.document())
            cur.setPosition(off.qt(s))
            cur.setPosition(off.qt(e), QTextCursor.KeepAnchor)
            sel.cursor = cur
            sels.append(sel)
        self._set_selections(sels)

    def _current_index(self, text: str) -> int:
        cur = self.editor.textCursor()
        if not cur.hasSelection():
            return -1
        off = self._offsets()
        span = (off.py(cur.selectionStart()), off.py(cur.selectionEnd()))
        idx = bisect_left(self._matches, span)
        return idx if idx < len(self._matches) and self._matches[idx] == span else -1

    def _update_status(self, text: str) -> None:
        if self._error:
            msg, red = self._error, True
        elif not self.find_edit.text():
            msg, red = "", False
        elif not self._matches:
            msg, red = "No results", True
        else:
            n = f"{len(self._matches)}{'+' if len(self._matches) >= MAX_MATCHES else ''}"
            i = self._current_index(text)
            msg, red = (f"{i + 1} of {n}" if i >= 0 else f"{n} found"), False
        if self._note and not self._error:
            msg = f"{msg}  ·  {self._note}" if msg else self._note
        if self.patch_replacer is not None and not self._error and self.find_edit.text():
            counted = self._patch_counts()
            if counted is not None:
                msg += f"  ·  in patches: {counted[0]} in {counted[1]} change(s)"
        self.lbl_status.setText(msg)
        self.lbl_status.setStyleSheet("color: #c0392b;" if red else "")

    # ------------------------------------------------------------ navigation
    def find_next(self) -> None:
        self._ensure_current_matches()
        self._note = ""
        self._goto(forward=True)

    def find_prev(self) -> None:
        self._ensure_current_matches()
        self._note = ""
        self._goto(forward=False)

    def _goto(self, forward: bool, include_caret: bool = False) -> None:
        if not self._matches:
            self._update_status(self._document_text())
            return
        text = self._document_text()
        off = self._offsets()
        cur = self.editor.textCursor()
        a, b = off.py(cur.selectionStart()), off.py(cur.selectionEnd())
        wrapped = False
        if forward:
            start_at = a if (include_caret or not cur.hasSelection()) else b
            idx = bisect_left(self._matches, (start_at, -1))
            if idx == len(self._matches):
                idx, wrapped = 0, True
        else:
            idx = bisect_left(self._matches, (a, -1)) - 1
            if idx < 0:
                idx, wrapped = len(self._matches) - 1, True
        s, e = self._matches[idx]
        sel = QTextCursor(self.editor.document())
        sel.setPosition(off.qt(s))
        sel.setPosition(off.qt(e), QTextCursor.KeepAnchor)
        self.editor.setTextCursor(sel)
        if hasattr(self.editor, "centerCursor"):          # QPlainTextEdit
            self.editor.centerCursor()
        else:                                              # QTextEdit / QTextBrowser
            self.editor.ensureCursorVisible()
        if wrapped:
            self._note = "wrapped around"
        self._update_status(text)
        self._note = ""

    # ------------------------------------------------------------ replace in the editor
    def _replacement_for(self, m: "re.Match") -> str:
        repl = self.replace_edit.text()
        return m.expand(repl) if self.btn_regex.isChecked() else repl

    def replace_current(self) -> None:
        if not self.can_replace_text:
            return
        pat = self._compile()
        if pat is None:
            return
        text = self._document_text()
        off = self._offsets()
        cur = self.editor.textCursor()
        if cur.hasSelection():
            a, b = off.py(cur.selectionStart()), off.py(cur.selectionEnd())
            m = pat.match(text, a)
            if m is not None and m.end() == b and b > a:
                try:
                    new = self._replacement_for(m)
                except (re.error, IndexError) as exc:
                    self.lbl_status.setText(f"Invalid replacement: {exc}")
                    return
                cur.insertText(new)
                self._refresh()
        self.find_next()

    def replace_all(self) -> None:
        if not self.can_replace_text:
            return
        pat = self._compile()
        if pat is None:
            return
        text = self._document_text()
        plan = []
        try:
            for m in pat.finditer(text):
                if m.end() > m.start():
                    plan.append((m.start(), m.end(), self._replacement_for(m)))
        except (re.error, IndexError) as exc:
            self.lbl_status.setText(f"Invalid replacement: {exc}")
            return
        if not plan:
            self.lbl_status.setText("No matches to replace")
            return
        off = self._offsets()
        # Edit only the matched spans (last to first) inside ONE edit block: the scroll
        # position and the caret stay where they are, and Ctrl+Z undoes it all at once.
        cur = QTextCursor(self.editor.document())
        cur.beginEditBlock()
        for s, e, new in reversed(plan):
            cur.setPosition(off.qt(s))
            cur.setPosition(off.qt(e), QTextCursor.KeepAnchor)
            cur.insertText(new)
        cur.endEditBlock()
        self._refresh()
        self.lbl_status.setText(f"Replaced {len(plan)} occurrence(s)   (Ctrl+Z undoes)")

    # ------------------------------------------------------------ replace inside the patches
    def _patch_args(self):
        return (self.patch_replacer, self._compile(), self.replace_edit.text(),
                not self.btn_regex.isChecked(), self.scope_combo.currentData())

    def _patch_counts(self) -> Optional[Tuple[int, int]]:
        rep, pat, repl, literal, scope = self._patch_args()
        if pat is None:
            return None
        try:
            return rep.replace(pat, repl, literal, scope, True)
        except (re.error, IndexError):
            return None

    def replace_in_patches(self) -> None:
        rep, pat, repl, literal, scope = self._patch_args()
        if pat is None:
            return
        try:
            total, touched = rep.replace(pat, repl, literal, scope, False)
        except (re.error, IndexError) as exc:
            self.lbl_status.setText(f"Invalid replacement: {exc}")
            return
        self.btn_patch_undo.setEnabled(rep.can_undo())
        self._refresh()
        self.lbl_status.setText(
            f"Replaced {total} occurrence(s) in {touched} change(s)" if total
            else "Nothing to replace in the patches")

    def undo_patch_replace(self) -> None:
        if self.patch_replacer is not None and self.patch_replacer.undo():
            self.btn_patch_undo.setEnabled(self.patch_replacer.can_undo())
            self._refresh()
            self.lbl_status.setText("Last replace undone")


def wrap_with_find_bar(editor, *, allow_replace: bool = False, patch_replacer=None) -> QWidget:
    """Container with the editor + a hidden find bar and the usual shortcuts."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    lay.addWidget(editor, 1)
    bar = FindBar(editor, allow_replace=allow_replace, patch_replacer=patch_replacer)
    bar.hide()
    lay.addWidget(bar)
    box.find_bar = bar
    box.editor = editor

    ctx = Qt.WidgetWithChildrenShortcut
    sc_find = QShortcut(QKeySequence(QKeySequence.Find), box)
    sc_find.setContext(ctx)
    sc_find.activated.connect(lambda: bar.open(replace=False))
    for seq, fn in (("F3", bar.find_next), ("Shift+F3", bar.find_prev)):
        sc = QShortcut(QKeySequence(seq), box)
        sc.setContext(ctx)
        sc.activated.connect(fn)
    if bar.can_replace:
        sc_rep = QShortcut(QKeySequence("Ctrl+H"), box)
        sc_rep.setContext(ctx)
        sc_rep.activated.connect(lambda: bar.open(replace=True))
    # Esc closes the bar - but only while it is open (otherwise a dialog's Esc keeps working)
    sc_esc = QShortcut(QKeySequence(Qt.Key_Escape), box)
    sc_esc.setContext(ctx)
    sc_esc.setEnabled(False)
    sc_esc.activated.connect(bar.close_bar)
    bar.visibilityChanged.connect(sc_esc.setEnabled)
    return box
