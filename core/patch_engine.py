"""Patch engine: deterministic application of validated Change objects.

Only `Change` objects come in - no XML (section 65). Multiple changes on
the same file are merged against the ORIGINAL content by locating every
target first, then splicing regions from bottom to top so earlier line
numbers never shift later ones (section 56).

Regex changes use re.sub(pattern, replacement, text) with re.MULTILINE
and support standard Python backreferences (\\1, \\g<name>, etc.).  They
are applied to the full file text in one pass, BEFORE any line-level
region ops so that line numbers remain valid.
"""
from __future__ import annotations

import re
from typing import Dict, List

from core.validator import locate
from models.change import Change


class PatchError(Exception):
    pass


def _apply_regex_change(text: str, ch: Change) -> str:
    """Apply a single regex replace-all change, returning the new file text."""
    pattern_src = ch.target.regex
    replacement = ch.content or ""
    try:
        pattern = re.compile(pattern_src, re.MULTILINE)
    except re.error as exc:
        raise PatchError(f"{ch.id}: invalid regex '{pattern_src}': {exc}") from exc
    new_text = pattern.sub(replacement, text)
    if new_text == text:
        raise PatchError(
            f"{ch.id}: regex '{pattern_src}' matched nothing in {ch.path} at apply time")
    return new_text


def merge_file_changes(original: str, changes: List[Change], settings) -> str:
    """Apply several region-level changes to one file deterministically.

    Regex changes are applied first (full-file re.sub passes), then
    line-level region ops are applied bottom-up to the result.
    """
    text = original

    # -- Pass 1: regex replace-all (applied to the full file in listed order) --
    regex_ops = [ch for ch in changes
                 if ch.operation == "replace" and ch.target.regex]
    for ch in regex_ops:
        text = _apply_regex_change(text, ch)

    # -- Pass 2: line-level region ops (replace / insert_before / insert_after) --
    region_ops: List[Change] = []
    non_regex_region = [ch for ch in changes
                        if ch.operation in ("replace", "insert_before", "insert_after")
                        and not ch.target.regex]
    for ch in non_regex_region:
        # re-locate against the (possibly regex-modified) text
        span, kind = locate(ch, text, settings)
        if span is None:
            raise PatchError(f"{ch.id}: target not found in {ch.path}")
        ch.located_range = span
        ch.match_kind = kind
        region_ops.append(ch)

    # overlap detection
    ordered = sorted(region_ops, key=lambda c: c.located_range)
    for a, b in zip(ordered, ordered[1:]):
        if b.located_range[0] < a.located_range[1]:
            raise PatchError(
                f"overlapping targets between {a.id} and {b.id} in {changes[0].path}")

    # splice from the end of the file upwards (use `text` which may have been
    # modified by regex passes above)
    lines = text.splitlines(keepends=True)
    for ch in sorted(region_ops, key=lambda c: c.located_range, reverse=True):
        start, end = ch.located_range
        body = ch.content or ""
        block = body if body.endswith("\n") or body == "" else body + "\n"
        if ch.operation == "replace":
            lines[start:end] = [block]
        elif ch.operation == "insert_before":
            lines[start:start] = [block]
        elif ch.operation == "insert_after":
            lines[end:end] = [block]
    return "".join(lines)


def plan_file_operations(changes: List[Change]) -> Dict[str, List[Change]]:
    """Group changes per file path, preserving order (apply sequence)."""
    groups: Dict[str, List[Change]] = {}
    for ch in changes:
        groups.setdefault(ch.path, []).append(ch)
    return groups
