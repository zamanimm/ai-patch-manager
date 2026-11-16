"""Recently opened projects, stored per USER (not per project, not in the app
folder - so the list survives app updates and is shared by every workspace).

    <user data dir>/recent_projects.json
    <user data dir> = ~/.ai-patch-manager        (override: AI_PATCH_MANAGER_HOME)

Pure Python / no Qt, so it is easy to test. Never raises: a missing or corrupt
file simply means an empty list.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Dict, List, Optional

APP_DATA_DIRNAME = ".ai-patch-manager"
FILE_NAME = "recent_projects.json"
MAX_RECENT = 20


def app_data_dir() -> str:
    """Per-user data dir of the application (created on first write)."""
    override = os.environ.get("AI_PATCH_MANAGER_HOME")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.expanduser("~"), APP_DATA_DIRNAME)


def _key(path: str) -> str:
    """Identity of a project folder (case-insensitive on Windows)."""
    return os.path.normcase(os.path.abspath(path))


class RecentProjects:
    def __init__(self, path: Optional[str] = None, max_items: int = MAX_RECENT):
        self.path = path or os.path.join(app_data_dir(), FILE_NAME)
        self.max_items = max_items
        self.last_error: str = ""

    # -- reading ---------------------------------------------------------------
    def load(self) -> List[Dict]:
        """Raw entries, most recent first. Bad / foreign data is skipped."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            self.last_error = f"recent projects unreadable: {exc}"
            return []
        items = data.get("projects") if isinstance(data, dict) else None
        out, seen = [], set()
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict) or not isinstance(it.get("path"), str) or not it["path"]:
                continue
            k = _key(it["path"])
            if k in seen:
                continue
            seen.add(k)
            out.append({"path": it["path"],
                        "name": str(it.get("name") or os.path.basename(it["path"].rstrip("/\\")) or it["path"]),
                        "last_opened": str(it.get("last_opened") or "")})
        return out[: self.max_items]

    def entries(self) -> List[Dict]:
        """Like load(), plus an 'exists' flag (checked now, not stored)."""
        out = self.load()
        for e in out:
            e["exists"] = os.path.isdir(e["path"])
        return out

    # -- writing ---------------------------------------------------------------
    def add(self, root: str, name: Optional[str] = None) -> None:
        """Put *root* on top (moving it if it was already listed)."""
        if not root:
            return
        root = os.path.abspath(root)
        items = [e for e in self.load() if _key(e["path"]) != _key(root)]
        items.insert(0, {
            "path": root,
            "name": name or os.path.basename(root.rstrip("/\\")) or root,
            "last_opened": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._save(items[: self.max_items])

    def remove(self, root: str) -> None:
        self._save([e for e in self.load() if _key(e["path"]) != _key(root)])

    def clear(self) -> None:
        self._save([])

    def contains(self, root: str) -> bool:
        return any(_key(e["path"]) == _key(root) for e in self.load())

    def _save(self, items: List[Dict]) -> None:
        """Atomic write (temp file + replace): a crash can't corrupt the list."""
        try:
            folder = os.path.dirname(self.path)
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".recent_", suffix=".tmp", dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"version": 1, "projects": items}, fh, indent=2)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            self.last_error = ""
        except OSError as exc:
            self.last_error = f"could not save recent projects: {exc}"
