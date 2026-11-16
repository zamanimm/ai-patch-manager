"""One shared "Wrap Text" setting for the code / text viewers.

View ▸ Wrap Text (Alt+Z), and a "Wrap" checkbox inside the file viewer and the line picker
(those dialogs are modal, so the main menu is not reachable while they are open).
The choice is remembered between launches.

New installations start with wrapping enabled; an explicit saved preference is respected.
Viewers opt in with follow_wrap(editor). Editable prose / console boxes always wrap.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import QPlainTextEdit

SETTINGS_KEY = "wrap_text"


class WrapState(QObject):
    changed = Signal(bool)

    def __init__(self, settings: Optional[QSettings] = None):
        super().__init__()
        self._settings = settings or QSettings("AIPatchManager", "AIPatchManager")
        self._on = bool(self._settings.value(SETTINGS_KEY, True, type=bool))

    def enabled(self) -> bool:
        return self._on

    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self._on:
            return                                  # idempotent: no signal loops between menu / checkboxes
        self._on = on
        self._settings.setValue(SETTINGS_KEY, on)
        self.changed.emit(on)


_STATE: Optional[WrapState] = None


def wrap_state() -> WrapState:
    global _STATE
    if _STATE is None:
        _STATE = WrapState()
    return _STATE


def reset_wrap_state_for_tests(settings: Optional[QSettings] = None) -> WrapState:
    global _STATE
    _STATE = WrapState(settings)
    return _STATE


def apply_wrap(editor, on: bool) -> None:
    """Wrap (at word boundaries, or anywhere for very long tokens) or scroll sideways."""
    if isinstance(editor, QPlainTextEdit):
        editor.setLineWrapMode(QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap)
    else:                                           # QTextEdit / QTextBrowser
        mode = type(editor).LineWrapMode
        editor.setLineWrapMode(mode.WidgetWidth if on else mode.NoWrap)
    editor.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere if on else QTextOption.WordWrap)


class _Follower(QObject):
    """Child of the editor: its connection disappears together with the editor."""

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def on_changed(self, on: bool) -> None:
        apply_wrap(self._editor, on)


def follow_wrap(editor) -> _Follower:
    """Give *editor* the current wrap setting and keep it in step with later changes."""
    follower = _Follower(editor)
    state = wrap_state()
    state.changed.connect(follower.on_changed)
    apply_wrap(editor, state.enabled())
    return follower
