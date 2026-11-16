"""Application icon (a source file with a red/green diff, an AI sparkle and a
'validated' check - the LLM proposes, the app validates, you approve).

Files live in <app>/assets/ (see assets/make_icons.py to regenerate them).
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
APP_USER_MODEL_ID = "AIPatchManager.DesktopApp"


def load_app_icon() -> QIcon:
    """Multi-size icon: Qt picks the best size (small ones are simplified)."""
    icon = QIcon()
    for size in ICON_SIZES:
        path = os.path.join(ASSETS_DIR, f"icon_{size}.png")
        if os.path.isfile(path):
            icon.addFile(path, QSize(size, size))
    if icon.isNull():                                  # PNGs missing: try the SVG
        svg = os.path.join(ASSETS_DIR, "icon.svg")
        if os.path.isfile(svg):
            icon = QIcon(svg)
    return icon


def set_windows_app_id() -> None:
    """On Windows, give the process its own identity so the taskbar shows OUR
    icon (not python.exe's) and groups the windows correctly."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
