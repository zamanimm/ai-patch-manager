"""'How to Use' tab: the user guide, shown inside the app.

The text comes straight from README.md (single source of truth - no second
copy of the documentation to keep in sync). Sections that only make sense on
a download page (installing, developer notes) are not shown here.
"""
from __future__ import annotations

import os
from typing import List

from qtpy.QtGui import QFont
from qtpy.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from core.controller import APP_DIR
from ui.find_bar import wrap_with_find_bar

README_PATH = os.path.join(APP_DIR, "README.md")
# README sections that are not part of the in-app guide
HIDDEN_SECTIONS = ("install and run", "for developers")

FALLBACK = """# How to Use

1. **Project** tab: open a project and tick the files the LLM should see.
2. **Prompt** tab: write the task, press *Generate* (Ctrl+G) and copy the prompt into your LLM.
3. **Response** tab: paste the LLM's answer and press *Parse + Validate*.
4. **Changes / Preview** tab: review every diff, then *Apply*.
5. **History** tab: select a transaction to undo/redo the whole Apply, or expand a file and select a `chg-...` row to undo/redo just that change. Older history entries support transaction-level undo only.

(The full guide is README.md in the application folder - it was not found.)
"""


def guide_markdown(text: str) -> str:
    """README text without the sections that do not belong in the app."""
    out: List[str] = []
    skipping = False
    for line in text.splitlines():
        if line.startswith("## "):
            skipping = line[3:].strip().lower() in HIDDEN_SECTIONS
        if not skipping:
            out.append(line)
    # a trailing '---' separator left behind by a removed section looks odd
    while out and out[-1].strip() in ("", "---"):
        out.pop()
    return "\n".join(out) + "\n"


class GuideView(QWidget):
    TAB_TITLE = "How to Use"

    def __init__(self, ctrl, main):
        super().__init__()
        self.ctrl, self.main = ctrl, main
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setReadOnly(True)
        font = QFont(self.browser.font())
        font.setPointSize(max(font.pointSize(), 10) + 1)
        self.browser.setFont(font)
        layout.addWidget(wrap_with_find_bar(self.browser))      # Ctrl+F
        self.reload()

    def reload(self):
        try:
            with open(README_PATH, "r", encoding="utf-8") as fh:
                text = guide_markdown(fh.read())
        except OSError:
            text = FALLBACK
        self.browser.setMarkdown(text)
        self.browser.moveCursor(self.browser.textCursor().MoveOperation.Start)
