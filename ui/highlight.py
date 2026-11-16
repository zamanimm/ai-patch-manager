"""Syntax highlighting for every code viewer of the app.

Uses Pygments (hundreds of languages) when it is installed; without it the
viewers simply show plain text - nothing else changes.

    CodeHighlighter(document, filename="app.py")   # source code
    CodeHighlighter(document, language="xml")      # prompts / responses
    DiffHighlighter(document)                      # unified diffs (per-file language)

Colours follow the application palette (light / dark).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QApplication

try:                                     # optional dependency
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
    from pygments.token import Token
    from pygments.util import ClassNotFound
    HAVE_PYGMENTS = True
except ImportError:                      # pragma: no cover - exercised via monkeypatch in tests
    HAVE_PYGMENTS = False

MAX_HIGHLIGHT_CHARS = 400_000            # bigger documents stay plain (keeps the UI fast)
Spans = List[List[Tuple[int, int, object]]]   # per line: (column, length, token type)

# category -> (light colour, dark colour, bold, italic)
_STYLE = {
    "comment":   ("#57606a", "#8b949e", False, True),
    "keyword":   ("#0000c0", "#569cd6", True,  False),
    "type":      ("#267f99", "#4ec9b0", False, False),
    "constant":  ("#0070c1", "#79c0ff", False, False),
    "builtin":   ("#0d6e6e", "#4ec9b0", False, False),
    "function":  ("#7a3e9d", "#dcdcaa", False, False),
    "class":     ("#267f99", "#4ec9b0", True,  False),
    "decorator": ("#af00db", "#c586c0", False, False),
    "tag":       ("#800000", "#569cd6", False, False),
    "attribute": ("#e50000", "#9cdcfe", False, False),
    "string":    ("#9a5b00", "#ce9178", False, False),
    "number":    ("#098658", "#b5cea8", False, False),
    "preproc":   ("#8a2be2", "#c586c0", False, False),
    "heading":   ("#0000c0", "#569cd6", True,  False),
}


def _build_rules():
    if not HAVE_PYGMENTS:
        return {}
    T = Token
    return {
        T.Comment: "comment", T.Comment.Preproc: "preproc", T.Comment.PreprocFile: "string",
        T.Keyword: "keyword", T.Keyword.Type: "type", T.Keyword.Constant: "constant",
        T.Operator.Word: "keyword",
        T.Name.Builtin: "builtin", T.Name.Function: "function", T.Name.Class: "class",
        T.Name.Decorator: "decorator", T.Name.Tag: "tag", T.Name.Attribute: "attribute",
        T.Name.Constant: "constant", T.Name.Namespace: "class", T.Name.Exception: "class",
        T.String: "string", T.String.Doc: "comment", T.Number: "number",
        T.Literal: "string", T.Generic.Heading: "heading", T.Generic.Subheading: "heading",
    }


_RULES = _build_rules()
_CAT_CACHE: Dict[object, Optional[str]] = {}
_FMT_CACHE: Dict[Tuple[str, bool], QTextCharFormat] = {}
_LEXER_CACHE: Dict[str, object] = {}


def is_dark_theme() -> bool:
    try:
        return QApplication.palette().base().color().lightness() < 128
    except Exception:
        return False


def _category(ttype) -> Optional[str]:
    if ttype in _CAT_CACHE:
        return _CAT_CACHE[ttype]
    t, cat = ttype, None
    while t is not None:                 # walk up: Name.Function.Magic -> Name.Function
        if t in _RULES:
            cat = _RULES[t]
            break
        t = t.parent
    _CAT_CACHE[ttype] = cat
    return cat


def token_format(ttype, dark: bool) -> Optional[QTextCharFormat]:
    cat = _category(ttype)
    if cat is None:
        return None
    key = (cat, dark)
    if key not in _FMT_CACHE:
        light, dk, bold, italic = _STYLE[cat]
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(dk if dark else light))
        if bold:
            fmt.setFontWeight(QFont.Bold)
        if italic:
            fmt.setFontItalic(True)
        _FMT_CACHE[key] = fmt
    return _FMT_CACHE[key]


# ------------------------------------------------------------------ lexers
# Pygments guesses Objective-C for *.m; in this tool's world it is MATLAB
_EXT_OVERRIDES = {".m": "matlab"}


def lexer_for_filename(name: str):
    if not HAVE_PYGMENTS or not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if base not in _LEXER_CACHE:
        lexer = None
        ext = "." + base.rsplit(".", 1)[-1].lower() if "." in base else ""
        if ext in _EXT_OVERRIDES:
            lexer = lexer_by_name(_EXT_OVERRIDES[ext])
        if lexer is None:
            try:
                lexer = get_lexer_for_filename(base, stripnl=False, ensurenl=False)
            except ClassNotFound:
                lexer = None
        _LEXER_CACHE[base] = lexer
    return _LEXER_CACHE[base]


def lexer_by_name(language: str):
    if not HAVE_PYGMENTS or not language:
        return None
    key = "name:" + language
    if key not in _LEXER_CACHE:
        try:
            _LEXER_CACHE[key] = get_lexer_by_name(language, stripnl=False, ensurenl=False)
        except ClassNotFound:
            _LEXER_CACHE[key] = None
    return _LEXER_CACHE[key]


def line_spans(text: str, lexer) -> Spans:
    """Token spans per line (columns are code-point indexes, like Python str)."""
    spans: Spans = [[]]
    if lexer is None or not text:
        return spans
    line = col = 0
    for _idx, ttype, value in lexer.get_tokens_unprocessed(text):
        if not value:
            continue
        parts = value.split("\n")
        for k, part in enumerate(parts):
            if k:
                line += 1
                col = 0
                spans.append([])
            if part:
                if _category(ttype):
                    spans[line].append((col, len(part), ttype))
                col += len(part)
    return spans


# ------------------------------------------------------------------ diffs
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def diff_spans(diff_text: str) -> Spans:
    """Per-line token spans for a unified diff, using each file's language.

    The OLD side (context + removed lines) and the NEW side (context + added
    lines) of every hunk are lexed separately, so the interleaving of removed
    and added lines never confuses the lexer."""
    lines = diff_text.splitlines()
    out: Spans = [[] for _ in range(len(lines) + 1)]
    lexer = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            lexer = lexer_for_filename(path.split("\t")[0])
            i += 1
            continue
        m = _HUNK.match(line)
        if not m:
            i += 1
            continue
        old_left = int(m.group(1)) if m.group(1) is not None else 1
        new_left = int(m.group(2)) if m.group(2) is not None else 1
        i += 1
        old_side: List[Tuple[int, str]] = []
        new_side: List[Tuple[int, str]] = []
        kinds: Dict[int, str] = {}
        while i < n and (old_left > 0 or new_left > 0):
            ln = lines[i]
            tag, body = ln[:1], ln[1:]
            if tag == "-":
                old_side.append((i, body)); kinds[i] = "-"; old_left -= 1
            elif tag == "+":
                new_side.append((i, body)); kinds[i] = "+"; new_left -= 1
            elif tag == "\\":
                pass
            else:
                old_side.append((i, body)); new_side.append((i, body)); kinds[i] = " "
                old_left -= 1; new_left -= 1
            i += 1
        if lexer is None:
            continue
        for side, want in ((old_side, "-"), (new_side, "+")):
            if not side:
                continue
            sp = line_spans("\n".join(b for _, b in side), lexer)
            for k, (idx, _b) in enumerate(side):
                if kinds[idx] == " " and want == "-":
                    continue                       # context lines use the NEW side
                out[idx] = [(c + 1, l, t) for c, l, t in (sp[k] if k < len(sp) else [])]
    return out


# ------------------------------------------------------------------ highlighters
class SpanHighlighter(QSyntaxHighlighter):
    """Paints pre-computed token spans (set_spans) onto a document."""

    def __init__(self, document):
        super().__init__(document)
        self._spans: Spans = []
        self._dark = is_dark_theme()

    def set_spans(self, spans: Spans) -> None:
        self._spans = spans
        self._dark = is_dark_theme()
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        n = self.currentBlock().blockNumber()
        if n >= len(self._spans) or not self._spans[n]:
            return
        astral = any(ord(c) > 0xFFFF for c in text)
        if astral:                                  # Qt columns are UTF-16 units
            u16 = [0]
            for ch in text:
                u16.append(u16[-1] + (2 if ord(ch) > 0xFFFF else 1))
        for col, length, ttype in self._spans[n]:
            fmt = token_format(ttype, self._dark)
            if fmt is None:
                continue
            if astral:
                end = min(col + length, len(text))
                self.setFormat(u16[col], u16[end] - u16[col], fmt)
            else:
                self.setFormat(col, length, fmt)


class CodeHighlighter(SpanHighlighter):
    """Highlights a document as one language; re-lexes when the text changes."""

    def __init__(self, document, *, filename: str = "", language: str = ""):
        super().__init__(document)
        self._lexer = None
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.relex)
        document.contentsChanged.connect(self._on_change)
        self.set_language(filename=filename, language=language)

    @property
    def active(self) -> bool:
        return self._lexer is not None

    def set_language(self, *, filename: str = "", language: str = "") -> None:
        self._lexer = lexer_by_name(language) if language else lexer_for_filename(filename)
        self.relex()

    def _on_change(self) -> None:
        if self._lexer is None or self._busy:
            return
        if self.document().characterCount() < 20_000:
            self.relex()                    # small: instantly, no flicker
        else:
            self._timer.start()             # big / typing: debounce

    def relex(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            text = self.document().toPlainText()
            if self._lexer is None or len(text) > MAX_HIGHLIGHT_CHARS:
                self.set_spans([])
            else:
                self.set_spans(line_spans(text, self._lexer))
        finally:
            self._busy = False


class DiffHighlighter(SpanHighlighter):
    """Highlight unified-diff lines while preserving their +/- backgrounds.

    Pygments token colours are merged *on top of* the full-line diff format.
    Calling ``super().highlightBlock`` here would replace each token's entire
    QTextCharFormat and erase the green/red background beneath that token.
    """

    def highlightBlock(self, text: str) -> None:
        base = QTextCharFormat()
        dark = self._dark
        if text.startswith(("+++", "---")):
            base.setForeground(QColor("#999999" if dark else "#666666"))
            base.setFontWeight(QFont.Bold)
        elif text.startswith("@@"):
            base.setForeground(QColor("#66d9ff" if dark else "#0066cc"))
            base.setFontWeight(QFont.Bold)
        elif text.startswith("+"):
            base.setForeground(QColor("#2eff2e" if dark else "#0a7a3d"))
            base.setBackground(QColor("#1a3a1a" if dark else "#c8f0d8"))
        elif text.startswith("-"):
            base.setForeground(QColor("#ff6b6b" if dark else "#b4232a"))
            base.setBackground(QColor("#3d1a1a" if dark else "#f8d7da"))
        else:
            base.setForeground(QColor("#cccccc" if dark else "#333333"))

        self.setFormat(0, len(text), base)

        block_number = self.currentBlock().blockNumber()
        if block_number >= len(self._spans):
            return
        spans = self._spans[block_number]
        if not spans:
            return

        # Qt uses UTF-16 positions, but the Pygments spans use Python indexes.
        astral = any(ord(ch) > 0xFFFF for ch in text)
        if astral:
            offsets = [0]
            for ch in text:
                offsets.append(offsets[-1] + (2 if ord(ch) > 0xFFFF else 1))

        for col, length, token_type in spans:
            syntax_format = token_format(token_type, dark)
            if syntax_format is None:
                continue
            # Preserve the entire diff line's red/green background. Only the
            # foreground/font aspects are replaced by the syntax token style.
            combined = QTextCharFormat(base)
            combined.merge(syntax_format)
            if astral:
                end = min(col + length, len(text))
                if col >= end:
                    continue
                self.setFormat(offsets[col], offsets[end] - offsets[col], combined)
            else:
                self.setFormat(col, length, combined)

    def refresh(self, diff_text: str) -> None:
        if not HAVE_PYGMENTS or len(diff_text) > MAX_HIGHLIGHT_CHARS:
            self.set_spans([])
        else:
            self.set_spans(diff_spans(diff_text))
