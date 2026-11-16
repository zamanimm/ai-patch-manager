"""Start-up helpers: run the app without a visible terminal and never fail silently.

* hide_console_if_owned()  Windows: hides the console window that was created just for this process
                           (double-clicking main.py). A terminal you started it from is left alone.
* spawn_detached()         `python main.py --detach`: starts the app on its own and returns the prompt.
* install_crash_handler()  with no terminal, an unexpected error would vanish - it is written to
                           <user data dir>/crash.log and shown in a dialog instead.

No Qt import at module level (Qt is only used to show a dialog when it is already running).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
from typing import List

from core.recent_projects import app_data_dir

DETACHED_ENV = "AI_PATCH_MANAGER_DETACHED"
DETACH_FLAG = "--detach"
APP_TITLE = "AI Patch Manager"


# ------------------------------------------------------------------ console (Windows)
def hide_console_if_owned() -> bool:
    """Hide this process's console window when it exists only for us.

    GetConsoleProcessList tells how many processes share the console: 1 = just us
    (the window was opened for us, e.g. by double-clicking), more = a shell is attached
    (the user started us from cmd / PowerShell) and that terminal is not ours to hide."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return False                                  # no console at all (pythonw): nothing to hide
        pids = (ctypes.c_uint * 8)()
        if kernel32.GetConsoleProcessList(pids, 8) <= 1:
            ctypes.windll.user32.ShowWindow(hwnd, 0)      # SW_HIDE
            return True
    except Exception:
        pass
    return False


# ------------------------------------------------------------------ --detach
def wants_detach(argv: List[str]) -> bool:
    return DETACH_FLAG in argv and not os.environ.get(DETACHED_ENV)


def spawn_detached(argv: List[str]) -> bool:
    """Start a second, fully independent copy of the app and return True when it started."""
    exe = sys.executable
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  close_fds=True, env=dict(os.environ, **{DETACHED_ENV: "1"}))
    if sys.platform == "win32":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")      # the no-console interpreter
        if os.path.isfile(w):
            exe = w
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    else:
        kwargs["start_new_session"] = True                          # survives closing the terminal
    args = [exe, os.path.abspath(argv[0])] + [a for a in argv[1:] if a != DETACH_FLAG]
    try:
        subprocess.Popen(args, **kwargs)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ errors must stay visible
def crash_log_path() -> str:
    return os.path.join(app_data_dir(), "crash.log")


def show_message(title: str, text: str) -> None:
    """A message the user will see even when there is no terminal."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(None, title, text)
            return
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)      # MB_ICONERROR
            return
        except Exception:
            pass
    if sys.stderr is not None:
        print(f"{title}: {text}", file=sys.stderr)


def _handle(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log = crash_log_path()
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{text}")
    except OSError:
        log = "(could not write the log file)"
    if sys.stderr is not None:                       # a terminal is there: normal behaviour too
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass
    last = text.strip().splitlines()[-1] if text.strip() else str(exc)
    show_message(f"{APP_TITLE} - unexpected error", f"{last}\n\nDetails were saved to:\n{log}")


def install_crash_handler() -> None:
    sys.excepthook = _handle
    if hasattr(threading, "excepthook"):
        threading.excepthook = lambda a: _handle(a.exc_type, a.exc_value, a.exc_traceback)
