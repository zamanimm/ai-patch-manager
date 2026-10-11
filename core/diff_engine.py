"""Diff engine (sections 2.2, 24, 46, 57).

Pure functions only: given original text + a validated Change, produce
the modified text and a unified diff. Preview uses these virtual results;
Apply writes them out. The two engines share this code but nothing else,
so Preview can NEVER touch real files (section 66).
"""
from __future__ import annotations

import difflib
import re
from typing import Dict, List, Optional

from models.change import Change


def compute_modified(original: str, change: Change) -> str:
    """Return the full new file content after applying *change* virtually."""
    op = change.operation
    if op == "create":
        return (change.content or "") + "\n"
    if op == "delete":
        raise ValueError("delete removes the whole file; no content result")
    if op == "rename":
        return original
    if op == "append":
        sep = "" if original.endswith("\n") or original == "" else "\n"
        return original + sep + (change.content or "") + "\n"
    if op == "prepend":
        return (change.content or "") + "\n" + original

    # region operations need a located range produced by the validator
    if change.located_range is None:
        raise ValueError(f"{change.id}: target was not located")
    start, end = change.located_range
    lines = original.splitlines(keepends=True)
    body = change.content or ""
    block = body if body.endswith("\n") or body == "" else body + "\n"

    if op == "replace":
        return "".join(lines[:start]) + block + "".join(lines[end:])
    if op == "insert_before":
        return "".join(lines[:start]) + block + "".join(lines[start:])
    if op == "insert_after":
        return "".join(lines[:end]) + block + "".join(lines[end:])
    raise ValueError(f"unknown operation {op}")


def unified_diff(old: str, new: str, path: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    return "".join(diff)


def fill_change_diffs(changes: List[Change], read_original) -> None:
    """Populate old_text/new_text/unified_diff on each valid region change.

    *read_original(rel_path)* returns current file text or None.
    Multiple changes against the same file are computed independently
    against the ORIGINAL version (section 56); the patch engine merges
    them deterministically at apply time.
    """
    originals: Dict[str, Optional[str]] = {}
    for ch in changes:
        if not ch.valid:
            continue
        if ch.path not in originals:
            originals[ch.path] = read_original(ch.path)
        old = originals[ch.path] or ""
        try:
            if ch.operation == "delete":
                ch.old_text, ch.new_text = old, ""
            elif ch.operation == "rename":
                ch.old_text, ch.new_text = "", ""
            else:
                new = compute_modified(old, ch)
                ch.old_text, ch.new_text = old, new
            ch.unified_diff = unified_diff(ch.old_text, ch.new_text, ch.path)
        except ValueError as exc:
            ch.valid = False
            ch.status = "invalid"
            ch.validation_errors.append(str(exc))


def file_diff_between(a: str, b: str, label: str) -> str:
    return unified_diff(a, b, label)
