"""Byte-exact direct snapshots, without the optional content-addressed blob cache.

Old Phase 2 snapshots used hard links to .blobs; those snapshot files remain
valid. Do not automatically remove .blobs or migrate existing backup folders.
"""
from __future__ import annotations

import hashlib
import os
import time

from core import file_manager


class BackupManager:
    def __init__(self, backups_root: str):
        self.backups_root = os.path.abspath(backups_root)
        os.makedirs(self.backups_root, exist_ok=True)

    def create_snapshot_dir(self, tx_id: str) -> str:
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(self.backups_root, f"{stamp}_{tx_id}")
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def legacy_backup_name(rel_path: str) -> str:
        """Previous filename convention; read-only compatibility."""
        return rel_path.replace("/", "__").replace("\\", "__")

    @staticmethod
    def backup_name(rel_path: str) -> str:
        """Collision-resistant filename for new snapshots.

        The old slash-to-__ conversion collides (a/b__c == a__b/c).
        Keep the readable component short and add a full SHA-256 path hash.
        """
        normalized = rel_path.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1][:65]
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"{name}--{digest}"

    def snapshot_path(self, snapshot_dir: str, rel_path: str, suffix: str) -> str:
        """Find a new-format or legacy snapshot without overwriting anything."""
        new = os.path.join(snapshot_dir, self.backup_name(rel_path) + suffix)
        if os.path.isfile(new):
            return new
        old = os.path.join(snapshot_dir, self.legacy_backup_name(rel_path) + suffix)
        return old if os.path.isfile(old) else new

    def _store_snapshot_file(self, snapshot_dir: str, rel_path: str,
                             source: str, suffix: str) -> str:
        name = self.backup_name(rel_path) + suffix
        destination = os.path.join(snapshot_dir, name)
        # Copy raw bytes and metadata. No shared blobs, no global cache.
        file_manager.copy_file(source, destination)
        return name

    def store_original(self, snapshot_dir: str, project_root: str,
                       rel_path: str) -> str:
        abs_src = file_manager.safe_join(project_root, rel_path)
        if not os.path.isfile(abs_src):
            return ""
        return self._store_snapshot_file(snapshot_dir, rel_path, abs_src, ".orig")

    def store_new(self, snapshot_dir: str, rel_path: str, abs_src: str) -> str:
        if not os.path.isfile(abs_src):
            return ""
        return self._store_snapshot_file(snapshot_dir, rel_path, abs_src, ".new")

    def capture_workspace(self, snapshot_dir: str, project_root: str,
                          rel_paths) -> int:
        count = 0
        for rel in rel_paths:
            try:
                abs_src = file_manager.safe_join(project_root, rel)
                if not os.path.isfile(abs_src):
                    continue
                if not os.path.isfile(self.snapshot_path(snapshot_dir, rel, ".orig")):
                    self.store_original(snapshot_dir, project_root, rel)
                count += 1
            except OSError:
                continue
        return count

    @staticmethod
    def _hash_of_file(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()

    def restore(self, snapshot_dir: str, project_root: str, rel_path: str,
                existed_before: bool) -> str:
        if not existed_before:
            file_manager.delete_file(file_manager.safe_join(project_root, rel_path))
            return ""
        src = self.snapshot_path(snapshot_dir, rel_path, ".orig")
        if not os.path.isfile(src):
            raise FileNotFoundError(f"missing backup snapshot: {src}")
        dst = file_manager.safe_join(project_root, rel_path)
        file_manager.copy_file(src, dst)
        return self._hash_of_file(dst)
