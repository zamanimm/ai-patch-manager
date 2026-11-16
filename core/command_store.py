"""Qt-independent, crash-safe storage for commands saved in a workspace.

The public file format remains ``.ai-patch-manager/saved_commands.json``.
Reading never rewrites existing files. Writes use a same-directory temporary
file and atomic replace so an interrupted save cannot truncate the original.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import List


class CommandStore:
    def __init__(self, project_root: str):
        self.path = (os.path.join(project_root, ".ai-patch-manager", "saved_commands.json")
                     if project_root else "")

    def read(self) -> dict:
        if not self.path:
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"Saved commands could not be read: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Saved commands must be a JSON object")
        return data

    def write(self, data: dict) -> None:
        if not self.path:
            return
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory,
                prefix=".commands-", suffix=".tmp", delete=False
            ) as fh:
                temp_path = fh.name
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def commands(self) -> List[str]:
        raw = self.read().get("commands", [])
        if not isinstance(raw, list):
            return []
        return [cmd for cmd in raw if isinstance(cmd, str) and cmd.strip()][:20]

    def last_command(self) -> str:
        cmd = self.read().get("last_command", "")
        return cmd if isinstance(cmd, str) else ""

    def remember(self, command: str) -> None:
        data = self.read()
        data["last_command"] = command
        self.write(data)

    def add(self, command: str) -> bool:
        if not command.strip():
            return False
        data = self.read()
        commands = self.commands()
        if command in commands:
            return False
        data["commands"] = [command, *commands][:20]
        self.write(data)
        return True

    def remove(self, command: str) -> bool:
        data = self.read()
        commands = self.commands()
        if command not in commands:
            return False
        data["commands"] = [cmd for cmd in commands if cmd != command]
        self.write(data)
        return True
