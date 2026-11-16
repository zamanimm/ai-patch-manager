"""Byte-exact direct snapshots, without the optional content-addressed blob cache.

Old Phase 2 snapshots used hard links to .blobs; those snapshot files remain
valid. Do not automatically remove .blobs or migrate existing backup folders.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import shutil
import tempfile
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
                       rel_path: str, allow_external: bool = False) -> str:
        abs_src = file_manager.safe_join(project_root, rel_path, allow_external=allow_external)
        if not os.path.isfile(abs_src):
            return ""
        return self._store_snapshot_file(snapshot_dir, rel_path, abs_src, ".orig")

    def store_new(self, snapshot_dir: str, rel_path: str, abs_src: str) -> str:
        if not os.path.isfile(abs_src):
            return ""
        return self._store_snapshot_file(snapshot_dir, rel_path, abs_src, ".new")

    def store_current(self, snapshot_dir: str, rel_path: str, abs_src: str,
                      state_id: str = "") -> str:
        """Store a versioned current state for a per-change undo/redo operation."""
        if not os.path.isfile(abs_src):
            return ""
        suffix = f".current-{state_id}" if state_id else ".current"
        return self._store_snapshot_file(snapshot_dir, rel_path, abs_src, suffix)


    # Reversible byte deltas are used only when they are materially smaller
    # than a full post-Apply snapshot. The immutable .orig snapshot remains the
    # trusted base, so the delta never becomes the only recovery source.
    DELTA_MAX_FILE_SIZE = 2 * 1024 * 1024
    DELTA_MAX_LINES = 20000
    DELTA_MAX_EDITS = 10000
    DELTA_MAX_RATIO = 0.70

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _atomic_write(path: str, payload: bytes) -> None:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".acp-artifact-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def store_redo_artifact(self, snapshot_dir: str, rel_path: str,
                            original_path: str, new_path: str,
                            old_hash: str, new_hash: str,
                            old_mode=None, new_mode=None) -> tuple[str, str]:
        """Store the smallest safe Redo representation for one file.

        Returns (strategy, artifact_filename). Strategies are: delta, snapshot,
        original, or delete. A full .new snapshot is the automatic fallback.
        """
        if not new_hash or not os.path.isfile(new_path):
            return "delete", ""

        if original_path:
            if not os.path.isfile(original_path):
                raise OSError(f"original backup snapshot is missing for {rel_path}")
            if self._hash_of_file(original_path) != old_hash:
                raise OSError(f"original backup snapshot failed hash verification for {rel_path}")
            if (old_mode is not None
                    and os.stat(original_path).st_mode & 0o7777 != old_mode):
                raise OSError(f"original backup snapshot failed permission verification for {rel_path}")

        if (original_path and old_hash == new_hash and old_mode == new_mode):
            return "original", ""

        delta_path = ""
        if original_path and os.path.isfile(original_path):
            try:
                old_size = os.path.getsize(original_path)
                new_size = os.path.getsize(new_path)
                if (old_size <= self.DELTA_MAX_FILE_SIZE
                        and new_size <= self.DELTA_MAX_FILE_SIZE
                        and self._hash_of_file(original_path) == old_hash
                        and self._hash_of_file(new_path) == new_hash):
                    with open(original_path, "rb") as fh:
                        old_bytes = fh.read()
                    with open(new_path, "rb") as fh:
                        new_bytes = fh.read()

                    # Diff at line granularity, then translate line ranges to
                    # exact byte offsets. Byte-wise SequenceMatcher can become
                    # quadratic on repetitive source files; line matching avoids
                    # that pathological cost and still preserves CRLF bytes.
                    if b"\x00" in old_bytes[:8192] or b"\x00" in new_bytes[:8192]:
                        raise ValueError("binary files use snapshot/original redo strategies")
                    old_lines = old_bytes.splitlines(keepends=True)
                    new_lines = new_bytes.splitlines(keepends=True)
                    if (len(old_lines) > self.DELTA_MAX_LINES
                            or len(new_lines) > self.DELTA_MAX_LINES):
                        raise ValueError("file has too many lines for delta generation")
                    old_offsets = [0]
                    new_offsets = [0]
                    for line in old_lines:
                        old_offsets.append(old_offsets[-1] + len(line))
                    for line in new_lines:
                        new_offsets.append(new_offsets[-1] + len(line))
                    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=True)
                    edits = []
                    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                        if tag != "equal":
                            edits.append({
                                "start": old_offsets[i1],
                                "end": old_offsets[i2],
                                "data": base64.b64encode(new_bytes[new_offsets[j1]:new_offsets[j2]]).decode("ascii"),
                            })
                    if len(edits) > self.DELTA_MAX_EDITS:
                        raise ValueError("file has too many edit regions for delta generation")
                    delta = {
                        "version": 1,
                        "base_size": len(old_bytes),
                        "base_sha256": old_hash,
                        "result_size": len(new_bytes),
                        "result_sha256": new_hash,
                        "edits": edits,
                    }
                    encoded = json.dumps(delta, separators=(",", ":"),
                                         ensure_ascii=True).encode("ascii")
                    # Delta must be meaningfully smaller, not merely a few
                    # bytes smaller, otherwise keep the simpler full snapshot.
                    if new_size > 0 and len(encoded) <= int(new_size * self.DELTA_MAX_RATIO):
                        name = self.backup_name(rel_path) + ".redo.delta.json"
                        delta_path = os.path.join(snapshot_dir, name)
                        self._atomic_write(delta_path, encoded)
                        # Verify the artifact by applying it before committing.
                        rebuilt = self.apply_redo_delta(snapshot_dir, name, original_path,
                                                        old_hash, new_hash)
                        if rebuilt != new_bytes:
                            raise OSError(f"redo delta verification failed for {rel_path}")
                        return "delta", name
            except (OSError, ValueError, TypeError, json.JSONDecodeError, MemoryError):
                # Delta generation is an optimization only. If it fails, remove
                # any incomplete artifact and use a verified full snapshot.
                if delta_path and os.path.isfile(delta_path):
                    try:
                        os.remove(delta_path)
                    except OSError:
                        pass

        artifact = self.store_new(snapshot_dir, rel_path, new_path)
        if not artifact:
            raise OSError(f"could not store post-Apply snapshot for {rel_path}")
        snapshot_path = os.path.join(snapshot_dir, artifact)
        if self._hash_of_file(snapshot_path) != new_hash:
            raise OSError(f"post-Apply snapshot failed hash verification for {rel_path}")
        if (new_mode is not None
                and os.stat(snapshot_path).st_mode & 0o7777 != new_mode):
            raise OSError(f"post-Apply snapshot failed permission verification for {rel_path}")
        return "snapshot", artifact

    def apply_redo_delta(self, snapshot_dir: str, artifact_file: str,
                         original_path: str, expected_base_hash: str,
                         expected_result_hash: str) -> bytes:
        """Load, validate and apply a reversible byte delta to its exact base."""
        if not artifact_file or os.path.basename(artifact_file) != artifact_file:
            raise ValueError("invalid redo delta artifact path")
        artifact_path = os.path.join(snapshot_dir, artifact_file)
        if not os.path.isfile(artifact_path) or not os.path.isfile(original_path):
            raise FileNotFoundError("redo delta or original snapshot is missing")
        with open(original_path, "rb") as fh:
            base = fh.read()
        base_hash = self._sha256(base)
        if base_hash != expected_base_hash:
            raise ValueError("redo delta base hash does not match the original snapshot")
        with open(artifact_path, "rb") as fh:
            raw = fh.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("redo delta artifact exceeds the safety limit")
        data = json.loads(raw.decode("ascii"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("unsupported redo delta format")
        if (data.get("base_sha256") != expected_base_hash
                or data.get("result_sha256") != expected_result_hash
                or data.get("base_size") != len(base)):
            raise ValueError("redo delta metadata does not match the transaction")
        edits = data.get("edits")
        if not isinstance(edits, list) or len(edits) > 100000:
            raise ValueError("invalid redo delta edit list")

        output = bytearray()
        cursor = 0
        for edit in edits:
            if not isinstance(edit, dict):
                raise ValueError("invalid redo delta edit")
            start, end = edit.get("start"), edit.get("end")
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or start < cursor or end < start or end > len(base)):
                raise ValueError("redo delta edits overlap or exceed the base file")
            encoded = edit.get("data", "")
            if not isinstance(encoded, str):
                raise ValueError("invalid redo delta payload")
            try:
                replacement = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("invalid base64 in redo delta") from exc
            output.extend(base[cursor:start])
            output.extend(replacement)
            cursor = end
        output.extend(base[cursor:])
        result = bytes(output)
        if len(result) != data.get("result_size"):
            raise ValueError("redo delta result size verification failed")
        if self._sha256(result) != expected_result_hash:
            raise ValueError("redo delta result hash verification failed")
        return result

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
                existed_before: bool, allow_external: bool = False) -> str:
        if not existed_before:
            file_manager.delete_file(file_manager.safe_join(
                project_root, rel_path, allow_external=allow_external))
            return ""
        src = self.snapshot_path(snapshot_dir, rel_path, ".orig")
        if not os.path.isfile(src):
            raise FileNotFoundError(f"missing backup snapshot: {src}")
        dst = file_manager.safe_join(project_root, rel_path, allow_external=allow_external)
        directory = os.path.dirname(dst) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".acp-restore-", suffix=".tmp", dir=directory)
        os.close(fd)
        try:
            shutil.copy2(src, temp_path)
            os.replace(temp_path, dst)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return self._hash_of_file(dst)
