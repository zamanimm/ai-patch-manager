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

from core.validator import (find_region_conflicts, locate,
                            locate_failure_message, region_span)
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

    # -- Pass 2: line-level region ops (replace / anchored append + prepend) --
    # (an anchored append / prepend may use ANY target mode, incl. regex; only a
    # regex *replace* is the replace-all pass above)
    region_ops: List[Change] = []
    non_regex_region = [ch for ch in changes
                        if ch.is_anchored_insert
                        or (ch.operation == "replace" and not ch.target.regex)]
    for ch in non_regex_region:
        # re-locate against the (possibly regex-modified) text
        span, kind = locate(ch, text, settings)
        if span is None:
            raise PatchError(f"{ch.id}: {locate_failure_message(ch, kind)}"
                             if kind else f"{ch.id}: target not found in {ch.path}")
        ch.located_range = span
        ch.match_kind = kind
        region_ops.append(ch)

    # overlap detection (inserts are zero-width, so before+after the same
    # line, or replace + insert next to it, are fine)
    for a_, b_ in find_region_conflicts(region_ops):
        raise PatchError(f"overlapping targets between {a_.id} and {b_.id} "
                         f"in {changes[0].path}")

    # splice from the end of the file upwards; equal positions keep the order
    # the LLM listed them in
    order = {id(ch): i for i, ch in enumerate(region_ops)}
    lines = text.splitlines(keepends=True)
    for ch in sorted(region_ops, key=lambda c: (region_span(c), order[id(c)]), reverse=True):
        start, end = ch.located_range
        body = ch.content or ""
        block = body if body.endswith("\n") or body == "" else body + "\n"
        if ch.operation == "replace":
            lines[start:end] = [block]
        elif ch.operation == "prepend":          # anchored: directly above the target
            lines[start:start] = [block]
        elif ch.operation == "append":           # anchored: directly below the target
            lines[end:end] = [block]
    return "".join(lines)


def compute_file_result(original: str, changes: List[Change], settings) -> str:
    """Compute one file's result using the same merge semantics as Apply.

    Kept in the patch engine so History can rebuild a file with one Change
    omitted without inventing an inverse textual patch.
    """
    region_ops = [c for c in changes if c.is_region_op]
    text = merge_file_changes(original, region_ops, settings) if region_ops else original
    for change in changes:
        if change.operation == "create":
            text = (change.content or "") + "\n"
        elif change.operation == "append" and not change.has_anchor:
            sep = "" if text.endswith("\n") or text == "" else "\n"
            text = text + sep + (change.content or "") + "\n"
        elif change.operation == "prepend" and not change.has_anchor:
            text = (change.content or "") + "\n" + text
    return text


def plan_file_operations(changes: List[Change]) -> Dict[str, List[Change]]:
    """Group changes per file path, preserving order (apply sequence)."""
    groups: Dict[str, List[Change]] = {}
    for ch in changes:
        groups.setdefault(ch.path, []).append(ch)
    return groups
