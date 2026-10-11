"""Project scanner: enumerate ALL files honouring include/exclude glob patterns.

Behaviour (fixes for "scan finds no files" / "binary files not detected"):
  * By default EVERY file type is included - text and binary alike
    (images, .mat, .mlx, .exe, pdf...). The prompt builder encodes binary
    content safely (base64) when a binary file is selected.
  * Patterns are matched against the POSIX-style relative path AND the bare
    file name, so "*.m", "**/*.m" and "src/**/*.py" all behave as expected.
  * A pattern that names only directories ("data", "vendor/", "**/build")
    excludes every file inside those directories at any depth.
  * Dot-directories (.git, .svn, ...) are skipped automatically during the
    walk; hidden files are otherwise allowed when they match include patterns.
"""
from __future__ import annotations

import os
import re
from typing import List

# Directories never worth scanning (skipped even if the user forgot to exclude them)
ALWAYS_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".idea", ".vscode",
                    "node_modules", ".venv", "venv"}


def _glob_to_regex(pat: str):
    """Compile a glob into a regex; returns (regex_for_path, regex_for_name)."""
    parts: List[str] = []
    i, n = 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if pat[i:i + 2] == "**":
                i += 2
                if i < n and pat[i] == "/":
                    # "**/" matches zero or more leading directories
                    parts.append("(?:.*/)?")
                    i += 1
                else:
                    parts.append(".*")
                continue
            parts.append("[^/]*")
        elif c == "?":
            parts.append("[^/]")
        elif c == "[":
            j = i + 1
            if j < n and pat[j] == "!":
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            if j >= n:
                parts.append(re.escape(c))
            else:
                cls = pat[i + 1:j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                parts.append("[" + cls.replace("\\", "\\\\") + "]")
                i = j
        else:
            parts.append(re.escape(c))
        i += 1
    return re.compile("(?s)" + "".join(parts) + r"\Z")


class _Compiled:
    __slots__ = ("raw", "rx_path", "rx_name", "dir_rx", "bare_dir_rx")

    def __init__(self, raw: str):
        p = raw.strip().replace(os.sep, "/").lstrip("./").strip("/")
        self.raw = p
        self.rx_path = _glob_to_regex(p)
        # bare-name variant: "src/*.m" also usable as "*.m" on the filename
        tail = p.split("/")[-1]
        self.rx_name = _glob_to_regex(tail) if tail else None
        # directory-prefix variants so "data", "data/" or "**/build" exclude
        # everything below a matching directory at any depth
        if p.endswith("/**"):
            core = p[:-3]
        elif p.endswith("/"):
            core = p.rstrip("/")
        elif "/" not in p and not any(ch in p for ch in "*?["):
            core = p                      # plain directory name
        else:
            core = None
        if core:
            self.dir_rx = _glob_to_regex(core)
            self.bare_dir_rx = _glob_to_regex(core.split("/")[-1])
        else:
            self.dir_rx = self.bare_dir_rx = None


def _compile(patterns) -> List[_Compiled]:
    out = []
    for pat in patterns or []:
        if isinstance(pat, _Compiled):
            out.append(pat)
            continue
        pat = (pat or "").strip()
        if pat:
            out.append(_Compiled(pat))
    return out


def _matches(rel_path: str, compiled: List[_Compiled]) -> bool:
    posix = rel_path.replace(os.sep, "/")
    parts = posix.split("/")
    name = parts[-1]
    for cp in compiled:
        if cp.rx_path.match(posix):
            return True
        # "*.ext" / "foo.ext" style patterns apply to the file name anywhere
        if "/" not in cp.raw and cp.rx_name and cp.rx_name.match(name):
            return True
    return False


def _dir_excluded(rel_dir: str, compiled: List[_Compiled]) -> bool:
    """True if directory `rel_dir` (posix, no trailing slash) is excluded."""
    if not rel_dir:
        return False
    parts = rel_dir.split("/")
    for cp in compiled:
        if cp.dir_rx and cp.dir_rx.match(rel_dir):
            return True
        # any ancestor segment equal to the bare dir name of the pattern
        if cp.bare_dir_rx:
            last = cp.raw.rstrip("/").split("/")[-1]
            for k in range(len(parts)):
                seg = parts[k]
                if cp.bare_dir_rx.match(seg):
                    if last == seg or k == len(parts) - 1:
                        return True
        if cp.rx_path.match(rel_dir) or cp.rx_path.match(rel_dir + "/x"):
            return True
    return False


def scan_project(root: str, include: List[str], exclude: List[str],
                 max_bytes: int = 2_000_000) -> List[str]:
    """Return sorted relative paths (forward slashes) of scannable files.

    ALL file types are detected - text and binary. An empty/absent include
    list means "everything" (only excludes apply).
    """
    root = os.path.abspath(root)
    inc = _compile(include)
    exc = _compile(exclude)
    results: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and not _dir_excluded(
                f"{rel_dir}/{d}" if rel_dir else d, exc)
        ]
        for fname in filenames:
            rel = f"{rel_dir}/{fname}" if rel_dir else fname
            if _matches(rel, exc):
                continue
            if inc and not _matches(rel, inc):
                continue
            try:
                if os.path.getsize(os.path.join(dirpath, fname)) > max_bytes:
                    continue
            except OSError:
                continue
            results.append(rel)
    return sorted(results)


def is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(2048)
        return b"\x00" in chunk
    except OSError:
        return True
