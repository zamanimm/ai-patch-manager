"""Small layout helpers for dynamic content that must never grow a window wider."""
from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtGui import QGuiApplication
from qtpy.QtWidgets import QLabel, QSizePolicy


def flexible_label(label: QLabel, *, selectable: bool = False) -> QLabel:
    """Allow long paths, status messages and unbroken tokens to wrap in the available width.

    QLabel otherwise uses its longest unbroken text as its minimum width, which
    can force an entire dialog/window to extend past the monitor.
    """
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    # Avoid replacing horizontal overflow with a huge vertical minimum size.
    # Full file paths remain available in the tooltip.
    label.setMaximumHeight(label.fontMetrics().lineSpacing() * 4 + 8)
    if selectable:
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def fit_to_screen(widget, width: int, height: int) -> None:
    """Choose an initial size that fits the available (taskbar-excluded) desktop."""
    screen = widget.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        widget.resize(width, height)
        return
    area = screen.availableGeometry()
    widget.resize(min(width, max(1, int(area.width() * 0.94))),
                  min(height, max(1, int(area.height() * 0.94))))
