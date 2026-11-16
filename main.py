#!/usr/bin/env python3
"""AI Patch Manager entry point.

LLM proposes; the application validates; the user approves; the application applies.

Qt is provided by PySide6 directly (QtPy is optional), or by PyQt6/PyQt5
through QtPy. When multiple bindings are installed, PySide6 is preferred,
then PyQt6, then PyQt5 unless QT_API selects another provider.
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    from core import startup

    if startup.wants_detach(argv):
        if startup.spawn_detached(argv):
            return 0
    startup.hide_console_if_owned()
    startup.install_crash_handler()

    try:
        from core.qt_binding import load_qt_modules
        load_qt_modules()
    except ImportError as exc:
        startup.show_message("AI Patch Manager - Qt dependency", str(exc))
        return 1

    from ui.main_window import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
