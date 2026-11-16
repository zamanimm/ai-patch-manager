"""Safe file access layer (sections 35, 36, 37).

Every path coming from an LLM response is normalised, resolved and
validated against the project root before anything touches disk.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
from dataclasses import dataclass
from typing import Optional


class PathSafetyError(Exception):
    pass


def normalize_rel_path(raw: str) -> str:
    """Normalise a relative path to forward slashes; reject suspicious input."""
    if raw is None:
        raise PathSafetyError("empty path")
    p = raw.strip().strip('"').strip("'")
    if not p:
        raise PathSafetyError("empty path")
    # absolute paths / drive letters are never allowed unless external_paths on
    if p.startswith("/") or p.startswith("\\"):
        raise PathSafetyError(f"absolute path not allowed: {p}")
    if len(p) >= 2 and p[1] == ":":          # C:\... or C:/...
        raise PathSafetyError(f"drive-letter path not allowed: {p}")
    p = p.replace("\\", "/")
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    out: list[str] = []
    for seg in parts:
        if seg == "..":
            if out:
                out.pop()                    # collapse; escape check happens on resolve
            else:
                raise PathSafetyError(f"path traversal not allowed: {raw}")
        else:
            out.append(seg)
    if not out:
        raise PathSafetyError(f"path resolves to root: {raw}")
    return "/".join(out)


def safe_join(root: str, rel: str, allow_external: bool = False) -> str:
    """Resolve *rel* inside *root*; raise if it escapes the project root."""
    root_abs = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_abs, rel))
    if not allow_external:
        if target != root_abs and not target.startswith(root_abs + os.sep):
            raise PathSafetyError(
                f"path escapes project root (enable External Paths to override): {rel}")
    return target


def read_text(abs_path: str) -> Optional[str]:
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except OSError:
        return None


def is_binary_file(abs_path: str) -> bool:
    """True when the file exists and looks binary (NUL byte in first 8 KB)."""
    try:
        with open(abs_path, "rb") as fh:
            return is_binary_bytes(fh.read(8192))
    except OSError:
        return False


def is_binary_bytes(data: bytes) -> bool:
    """Heuristic: NUL byte in the first 8 KB => treat as binary."""
    return b"\x00" in data[:8192]


@dataclass(frozen=True)
class PromptFileSnapshot:
    """Fresh on-disk content and raw-byte metadata for one selected file."""
    content: str
    sha256: str
    size: int
    is_binary: bool


def _file_signature(stat: os.stat_result) -> tuple:
    """Detect replacement or edits while a selected file is being read."""
    return (stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


def read_prompt_snapshot(abs_path: str,
                         include_binary: bool = False) -> PromptFileSnapshot:
    """Always read the selected file directly from disk; no content cache.

    Use the same open handle for hashing and contents. Detect concurrent edits
    and retry once to avoid including inconsistent source. Large binary files
    are hashed in chunks and omitted unless the user enables base64 inclusion.
    All I/O failures propagate to the prompt builder for an actionable error.
    """
    for attempt in range(2):
        with open(abs_path, "rb") as fh:
            before = os.fstat(fh.fileno())
            first = fh.read(8192)
            binary = is_binary_bytes(first)
            digest = hashlib.sha256()
            digest.update(first)
            size_read = len(first)
            chunks = None if (binary and not include_binary) else [first]

            while True:
                block = fh.read(1024 * 1024)
                if not block:
                    break
                size_read += len(block)
                digest.update(block)
                if chunks is not None:
                    chunks.append(block)

            after = os.fstat(fh.fileno())

        # Recheck the path, too: the file may have been replaced or renamed
        # while its original handle was still readable.
        current = os.stat(abs_path)
        if (_file_signature(before) == _file_signature(after)
                == _file_signature(current)
                and size_read == after.st_size):
            break
        if attempt == 1:
            raise OSError(f"Selected file changed while being read: {abs_path}")

    sha256 = digest.hexdigest()
    size = size_read
    if binary:
        if include_binary:
            payload = base64.b64encode(b"".join(chunks or [])).decode("ascii")
            content = "[binary file: base64 follows]\n" + payload
        else:
            content = f"[binary file omitted: {size:,} bytes; sha256={sha256}]"
    else:
        content = b"".join(chunks or []).decode("utf-8", errors="replace")
    return PromptFileSnapshot(content, sha256, size, binary)


def read_text_any(abs_path: str, include_binary: bool = False) -> Optional[str]:
    """Read current file content for previews/prompts without caching."""
    try:
        return read_prompt_snapshot(abs_path, include_binary).content
    except OSError:
        return None


def write_text(abs_path: str, content: str) -> None:
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp = abs_path + ".acp-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    os.replace(tmp, abs_path)


def delete_file(abs_path: str) -> None:
    if os.path.isfile(abs_path):
        os.remove(abs_path)


def rename_file(abs_src: str, abs_dst: str) -> None:
    os.makedirs(os.path.dirname(abs_dst), exist_ok=True)
    os.replace(abs_src, abs_dst)


def copy_file(abs_src: str, abs_dst: str) -> None:
    os.makedirs(os.path.dirname(abs_dst), exist_ok=True)
    shutil.copy2(abs_src, abs_dst)


def hash_file_or_text(abs_path: str) -> str:
    """SHA256 of the file's RAW BYTES (works for text and binary files);
    empty string when the file does not exist."""
    try:
        h = hashlib.sha256()
        with open(abs_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""
