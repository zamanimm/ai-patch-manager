"""Safe file access layer (sections 35, 36, 37).

Every path coming from an LLM response is normalised, resolved and
validated against the project root before anything touches disk.
"""
from __future__ import annotations

import hashlib
import os
import shutil
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


def read_text_any(abs_path: str) -> Optional[str]:
    """Read a file for PROMPT inclusion.

    Unlike read_text(), this also supports BINARY files: their raw bytes are
    base64-encoded and wrapped in an XML-safe marker so the LLM context stays
    valid UTF-8 text:

        [binary file: base64 follows]
        <base64 payload...>

    Returns None when the file does not exist / cannot be read.
    """
    try:
        with open(abs_path, "rb") as fh:
            data = fh.read()
    except (FileNotFoundError, IsADirectoryError):
        return None
    except OSError:
        return None
    if is_binary_bytes(data):
        import base64
        payload = base64.b64encode(data).decode("ascii")
        return ("[binary file: base64 follows]\n" + payload)
    return data.decode("utf-8", errors="replace")


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
