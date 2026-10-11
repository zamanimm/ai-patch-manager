#!/usr/bin/env python3
"""AI Patch Manager - entry point.

LLM proposes; the application validates; the user approves;
the application applies.

Usage:
    python main.py
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6 import QtWidgets  # noqa: F401
    except ImportError:
        print("PySide6 is required for the desktop UI.\n"
              "Install dependencies first:\n\n"
              "    pip install -r requirements.txt\n", file=sys.stderr)
        return 1
    from ui.main_window import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
