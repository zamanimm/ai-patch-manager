"""Persist recoverable UI errors for diagnosis without crashing the desktop app."""
from __future__ import annotations

import os
import time
import traceback

from core.recent_projects import app_data_dir

MAX_LOG_BYTES = 2 * 1024 * 1024


def record_error(action: str, error: Exception, path: str = "") -> str:
    """Append a traceback to a bounded per-user log; return its path or ''.

    A diagnostic write failure must never replace the original application error.
    """
    target = path or os.path.join(app_data_dir(), "ui_errors.log")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        # Preserve the last portion of the log rather than growing without bound.
        if os.path.isfile(target) and os.path.getsize(target) > MAX_LOG_BYTES:
            with open(target, "rb") as fh:
                fh.seek(-MAX_LOG_BYTES // 2, os.SEEK_END)
                tail = fh.read().decode("utf-8", errors="replace")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("[Earlier diagnostics trimmed]\n" + tail)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} : {action} ===\n")
            fh.write("".join(traceback.format_exception(type(error), error, error.__traceback__)))
        return target
    except OSError:
        return ""
