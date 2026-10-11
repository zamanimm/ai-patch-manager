"""Git integration (section 34) - optional, subprocess-based, read-mostly.

Git complements the internal History/Backup system; it never replaces it.
"""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional


class GitManager:
    def __init__(self, project_root: str):
        self.root = project_root

    def is_repo(self) -> bool:
        return os.path.isdir(os.path.join(self.root, ".git"))

    def _run(self, args: List[str]) -> Optional[str]:
        if not self.is_repo():
            return None
        try:
            proc = subprocess.run(
                ["git", *args], cwd=self.root, capture_output=True, text=True,
                timeout=20, encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                return f"git {args[0]} failed:\n{proc.stderr.strip()}"
            return proc.stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"git error: {exc}"

    def status(self) -> Optional[str]:
        return self._run(["status", "--short"])

    def diff(self, path: Optional[str] = None) -> Optional[str]:
        args = ["diff"]
        if path:
            args += ["--", path]
        return self._run(args)

    def restore_file(self, path: str) -> Optional[str]:
        """git restore <path> - explicit user action only."""
        return self._run(["restore", "--", path])
