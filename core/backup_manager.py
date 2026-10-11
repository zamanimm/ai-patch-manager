"""Backup manager (sections 28, 58, 60).

Every Apply transaction snapshots the ORIGINAL bytes of each touched file
into storage/backups/<YYYY-MM-DD_HH-MM-SS>/ before anything is written.
Metadata (original hash / new hash / timestamp / transaction id) lives in
the Transaction JSON written by the history manager.
"""
from __future__ import annotations

import os
import time

from core import file_manager
from models.change import sha256_text


class BackupManager:
    def __init__(self, backups_root: str):
        self.backups_root = backups_root
        os.makedirs(backups_root, exist_ok=True)

    def create_snapshot_dir(self, tx_id: str) -> str:
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(self.backups_root, f"{stamp}_{tx_id}")
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def backup_name(rel_path: str) -> str:
        """Flatten a relative path into one safe filename inside the snapshot."""
        return rel_path.replace("/", "__").replace("\\", "__")

    def store_original(self, snapshot_dir: str, project_root: str, rel_path: str) -> str:
        """Copy the current file (raw BYTES - text or binary) into the snapshot;
        returns backup filename ('' if absent)."""
        abs_src = file_manager.safe_join(project_root, rel_path)
        if not os.path.isfile(abs_src):
            return ""
        name = self.backup_name(rel_path)
        out = os.path.join(snapshot_dir, name + ".orig")
        file_manager.copy_file(abs_src, out)
        return os.path.basename(out)

    def capture_workspace(self, snapshot_dir: str, project_root: str,
                          rel_paths) -> int:
        """Byte-exact copies of every project file into a baseline snapshot.

        Uses the SAME '<flattened path>.orig' naming as apply-time backups so
        history diffs can read any file's state at any recorded transaction.
        Returns the number of files captured."""
        count = 0
        for rel in rel_paths:
            try:
                abs_src = file_manager.safe_join(project_root, rel)
                if not os.path.isfile(abs_src):
                    continue
                out = os.path.join(snapshot_dir, self.backup_name(rel) + ".orig")
                if not os.path.isfile(out):
                    file_manager.copy_file(abs_src, out)
                count += 1
            except OSError:
                continue
        return count

    @staticmethod
    def _hash_of_file(path: str) -> str:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()

    def restore(self, snapshot_dir: str, project_root: str, rel_path: str,
                existed_before: bool) -> str:
        """Roll one file back from its snapshot (byte-exact); verify integrity
        via hash. Returns sha256 of the restored content ("" if deleted)."""
        if not existed_before:
            abs_target = file_manager.safe_join(project_root, rel_path)
            file_manager.delete_file(abs_target)
            return ""
        name = self.backup_name(rel_path) + ".orig"
        src = os.path.join(snapshot_dir, name)
        dst = file_manager.safe_join(project_root, rel_path)
        file_manager.copy_file(src, dst)
        return self._hash_of_file(dst)
