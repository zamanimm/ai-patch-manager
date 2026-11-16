#!/usr/bin/env python3
"""AI Patch Manager - entry point.

LLM proposes; the application validates; the user approves;
the application applies.

Usage:
    python main.py              run (a terminal you started it from stays yours)
    python main.py --detach     run on its own and give the terminal back
    Windows: double-click "AI Patch Manager.pyw" - no terminal window at all
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    from core import startup

    if startup.wants_detach(argv):                    # --detach: start independently, return the prompt
        if startup.spawn_detached(argv):
            return 0
    startup.hide_console_if_owned()                   # double-clicked main.py: no console window behind the app
    startup.install_crash_handler()                   # errors are shown / logged even without a terminal

    try:
        from PySide6 import QtWidgets  # noqa: F401
    except ImportError:
        startup.show_message(
            "AI Patch Manager",
            "PySide6 is required for the desktop UI.\n\n"
            "Install the dependencies first:\n\n    pip install -r requirements.txt")
        return 1
    from ui.main_window import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
