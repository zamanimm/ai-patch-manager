"""'Open with…': open a file in a program the user chooses, and remember the choices.

The programs are remembered per user (not per project, not in the app folder):

    <user data dir>/open_with.json      (user data dir = ~/.ai-patch-manager, see recent_projects.py)

The program is started directly with the file path as ONE argument (no shell is involved), detached from
this application. Pure Python / no Qt, so it is easy to test; it never raises - problems come back as text.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple

from core.recent_projects import app_data_dir

FILE_NAME = "open_with.json"
MAX_PROGRAMS = 8
WINDOWS_PROGRAM_EXTS = (".exe", ".com", ".bat", ".cmd")


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def display_name(path: str) -> str:
    """'C:\\Tools\\Notepad++\\notepad++.exe' -> 'notepad++', '/Applications/Sublime Text.app' -> 'Sublime Text'."""
    base = path.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1] or path      # either separator, on any OS
    stem, ext = os.path.splitext(base)
    return stem if ext.lower() in (".app",) + WINDOWS_PROGRAM_EXTS and stem else base


class OpenWith:
    def __init__(self, path: str = "", max_items: int = MAX_PROGRAMS):
        self.path = path or os.path.join(app_data_dir(), FILE_NAME)
        self.max_items = max_items
        self.last_error = ""

    # -- remembered programs ---------------------------------------------------
    def load(self) -> List[Dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            self.last_error = f"saved programs unreadable: {exc}"
            return []
        items = data.get("programs") if isinstance(data, dict) else None
        out, seen = [], set()
        for it in items if isinstance(items, list) else []:
            p = it.get("path") if isinstance(it, dict) else None
            if not isinstance(p, str) or not p or _key(p) in seen:
                continue
            seen.add(_key(p))
            out.append({"path": p, "name": display_name(p)})
        return out[: self.max_items]

    def programs(self) -> List[Dict]:
        """Saved programs, most recently used first, each with an 'exists' flag (checked now)."""
        out = self.load()
        for p in out:
            p["exists"] = os.path.exists(p["path"])
        return out

    def add(self, program: str) -> None:
        if not program:
            return
        items = [p for p in self.load() if _key(p["path"]) != _key(program)]
        items.insert(0, {"path": os.path.abspath(program)})
        self._save(items[: self.max_items])

    def remove(self, program: str) -> None:
        self._save([p for p in self.load() if _key(p["path"]) != _key(program)])

    def clear(self) -> None:
        self._save([])

    def _save(self, items: List[Dict]) -> None:
        try:
            folder = os.path.dirname(self.path)
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".openwith_", suffix=".tmp", dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"version": 1, "programs": [{"path": i["path"]} for i in items]}, fh, indent=2)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            self.last_error = ""
        except OSError as exc:
            self.last_error = f"could not save the program list: {exc}"

    # -- launching ---------------------------------------------------------------
    @staticmethod
    def check_program(program: str) -> str:
        """'' when the program can be started, otherwise a short reason."""
        if not program or not os.path.exists(program):
            return f"Program not found:\n{program}"
        if sys.platform == "win32":
            if not (os.path.isfile(program) and program.lower().endswith(WINDOWS_PROGRAM_EXTS)):
                return "Please choose a program (.exe, .com, .bat or .cmd)."
        elif sys.platform == "darwin" and program.rstrip("/").lower().endswith(".app") and os.path.isdir(program):
            return ""
        elif not (os.path.isfile(program) and os.access(program, os.X_OK)):
            return f"This file is not an executable program:\n{program}"
        return ""

    @staticmethod
    def command_for(program: str, file_path: str) -> List[str]:
        if sys.platform == "darwin" and program.rstrip("/").lower().endswith(".app"):
            return ["open", "-a", program, file_path]          # macOS application bundle
        return [program, file_path]

    def launch(self, program: str, file_path: str) -> Tuple[bool, str]:
        """Open *file_path* with *program*. Returns (ok, error text)."""
        if not file_path or not os.path.isfile(file_path):
            return False, "The file no longer exists on disk."
        problem = self.check_program(program)
        if problem:
            return False, problem
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      close_fds=True)
        if sys.platform == "win32":
            kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        else:
            kwargs["start_new_session"] = True                  # independent of this application
        try:
            subprocess.Popen(self.command_for(program, file_path), **kwargs)
        except OSError as exc:
            return False, f"Could not start {display_name(program)}:\n{exc}"
        self.add(program)                                       # worked: remember it
        return True, ""
