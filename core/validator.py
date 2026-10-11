"""Target location + validation pipeline (sections 20-23, 54, 55).

Addressing priority:
  1. Exact match      2. Hash verification of the matched block
  3. Context match    4. Line range            5. Fuzzy match (opt-in)

Nothing fuzzy is ever applied silently - a fuzzy hit is marked so the UI
can demand explicit user approval. Conflict detection compares the hash
of the file as it is NOW with the state used to build the prompt.
"""
from __future__ import annotations

import difflib
import re
from typing import Dict, List, Optional, Tuple

from core import file_manager
from models.change import Change, sha256_text


def _norm(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())


def locate(change: Change, file_text: str, settings, file_path: str = "") -> Tuple[Optional[Tuple[int, int]], str]:
    """Return ((start_idx, end_idx) zero-based half-open, match_kind) or (None, '')."""
    lines = file_text.splitlines(keepends=True)
    joined = "".join(lines)
    target = change.target

    # -- 0. symbol (Tree-Sitter structural targeting) -------------------------
    if target.symbol:
        try:
            from core.symbol_resolver import SymbolResolver
            resolver = _get_resolver()
            # Use change.path as file_path if not provided
            lookup_path = file_path or change.path
            span, kind = resolver.resolve(file_text, lookup_path, target.symbol)
            if span is not None:
                return span, f"symbol:{kind}"
            # Symbol resolution failed - return error or fall through
            if kind in ("no_tree_sitter", "no_grammar"):
                # Tree-Sitter not available: fall through to other modes, but
                # if the change has no other way to find its target, say why
                if not (target.match or target.regex or target.line_start):
                    return None, f"symbol_error:{kind}"
            else:
                # Symbol-specific error
                return None, f"symbol_error:{kind}"
        except Exception:
            # Symbol resolution error - fall through to other modes
            pass

    # -- 1. exact match (priority 2) -------------------------------------------
    if settings.exact_match and target.match:
        needle = _norm(target.match)
        # try raw first, then whitespace-normalised comparison line-by-line
        idx = joined.find(target.match)
        if idx != -1:
            return _span_from_offset(lines, idx, idx + len(target.match)), "exact"
        norm_joined = _norm(joined)
        idx = norm_joined.find(needle)
        if idx != -1:
            span = _map_norm_span_to_lines(file_text, lines, needle, idx)
            if span:
                return span, "exact"

    # -- 2. regex (replace-all; Python re with backreferences - priority 3) --
    if target.regex:
        try:
            pattern = re.compile(target.regex, re.MULTILINE)
        except re.error as exc:
            # invalid pattern - the validator will surface this as an error
            return None, f"regex_error:{exc}"
        if pattern.search(joined):
            # For locate purposes we return the span of the FIRST match so the
            # diff engine can show something meaningful. apply_regex() does the
            # actual replace-all later.
            m = pattern.search(joined)
            return _span_from_offset(lines, m.start(), m.end()), "regex"
        return None, ""

    # -- 3. line range (priority 4; used before context so context can verify it) ------
    if target.line_start:
        start = max(1, target.line_start)
        end = target.line_end or start
        start_idx = min(start - 1, len(lines))
        end_idx = min(end, len(lines))
        span = (start_idx, end_idx)
        block = "".join(lines[start_idx:end_idx])

        if target.hash and settings.hash_validation:
            if sha256_text(block.strip()) == target.hash.strip().lower():
                return span, "line+hash"
            # hash mismatch -> possible CONFLICT handled by caller via conflict check
        if target.match and settings.context_match:
            if _similarity(_norm(target.match), _norm(block)) >= 0.95:
                return span, "context"
        if not target.match:
            return span, "line"

    # -- 4. fuzzy (priority 5; optional, never silent) ----------------------------------
    if settings.fuzzy_match and target.match:
        span = _fuzzy_locate(lines, target.match, settings.fuzzy_threshold)
        if span:
            return span, "fuzzy"

    return None, ""


_RESOLVER = None


def _get_resolver():
    """One shared resolver, so grammars are loaded once (not on every call)."""
    global _RESOLVER
    if _RESOLVER is None:
        from core.symbol_resolver import SymbolResolver
        _RESOLVER = SymbolResolver()
    return _RESOLVER


def _symbol_error_text(ch: Change, kind: str) -> str:
    sym = ch.target.symbol
    desc = f"{sym.type} '{sym.name}'" if sym else "symbol"
    if sym and sym.class_name:
        desc += f" in class '{sym.class_name}'"
    texts = {
        "not_found": f"symbol not found: {desc} does not exist in {ch.path}",
        "ambiguous": (f"symbol is ambiguous: 2+ matches for {desc} in {ch.path} "
                      f"(add <class> or <parameters> to disambiguate)"),
        "parse_error": (f"cannot resolve {desc}: {ch.path} has syntax errors "
                        f"(Tree-Sitter found ERROR nodes)"),
        "unsupported_language": (f"cannot resolve {desc}: no Tree-Sitter grammar/adapter "
                                 f"for {ch.path} (supported: Python, JS/TS/TSX, Java, C/C++)"),
        "unsupported_symbol_type": (f"unsupported symbol type '{sym.type if sym else ''}' "
                                    f"for {ch.path}"),
        "no_tree_sitter": ("Tree-Sitter is not installed "
                           "(pip install tree-sitter tree-sitter-python tree-sitter-javascript "
                           "tree-sitter-typescript tree-sitter-java tree-sitter-c tree-sitter-cpp)"),
        "no_grammar": _no_grammar_text(desc, ch.path),
    }
    return texts.get(kind, f"symbol resolution failed ({kind}) for {desc}")


def _no_grammar_text(desc: str, path: str) -> str:
    r = _get_resolver()
    pkg = r.engine.pip_package_for(r.last_language)
    hint = f" Fix: pip install {pkg}" if pkg else ""
    return (f"cannot resolve {desc}: the Tree-Sitter grammar for "
            f"'{r.last_language}' could not be loaded ({r.last_error}).{hint}  "
            f"Run check_treesitter.py for a full diagnosis.")


def _span_from_offset(lines, start_off: int, end_off: int):
    pos = 0
    start_idx = end_idx = 0
    for i, ln in enumerate(lines):
        nxt = pos + len(ln)
        if pos <= start_off < nxt:
            start_idx = i
        if pos < end_off <= nxt:
            end_idx = i + 1
        pos = nxt
    return (start_idx, max(end_idx, start_idx + 1))


def _map_norm_span_to_lines(orig_text: str, lines, needle: str, norm_idx: int):
    """Map a match found in the rstrip-normalised text back to line spans."""
    norm = _norm(orig_text)
    # count how many original lines correspond to norm prefix up to norm_idx
    consumed = 0
    start_line = 0
    for i, ln in enumerate(lines):
        stripped_len = len(ln.rstrip("\n").rstrip())
        consumed += stripped_len + 1          # +1 for the \n in normalised text
        if consumed > norm_idx:
            start_line = i
            break
    needle_lines = max(1, needle.count("\n") + 1)
    return (start_line, min(start_line + needle_lines, len(lines)))


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _fuzzy_locate(lines, match_text: str, threshold: float):
    needle_lines = match_text.splitlines()
    n = len(needle_lines)
    best_span, best_ratio = None, 0.0
    window = "".join(l.rstrip("\n") for l in needle_lines)
    sm = difflib.SequenceMatcher(None, window, "", autojunk=False)
    for i in range(len(lines) - n + 1):
        cand = "\n".join(l.rstrip("\n") for l in lines[i:i + n])
        sm.set_seqs(window, cand)
        ratio = sm.ratio()
        if ratio > best_ratio:
            best_ratio, best_span = ratio, (i, i + n)
    if best_span and best_ratio >= threshold:
        return best_span
    return None


# ---------------------------------------------------------------------------
# Validation pipeline (section 23)
# ---------------------------------------------------------------------------

class ValidationResult:
    def __init__(self):
        self.ok_changes: List[Change] = []
        self.errors: List[str] = []           # human readable, per change
        self.conflicts: List[str] = []        # change ids with content conflicts
        self.binary_skipped: List[str] = []   # changes touching binary files

    @property
    def all_valid(self) -> bool:
        return not self.errors and not self.conflicts


def _is_binary_file(abs_path: str) -> bool:
    try:
        with open(abs_path, "rb") as fh:
            return file_manager.is_binary_bytes(fh.read(8192))
    except OSError:
        return False


def validate_changes(changes: List[Change], project_root: str, settings,
                     snapshot_hashes: Optional[Dict[str, str]] = None) -> ValidationResult:
    """Full pre-preview validation. A change that fails NEVER reaches apply."""
    result = ValidationResult()
    seen_paths: Dict[str, List[str]] = {}

    for ch in changes:
        errs: List[str] = []

        # --- path safety (sections 36/37) ---------------------------------
        try:
            rel = file_manager.normalize_rel_path(ch.path)
            ch.path = rel
            abs_path = file_manager.safe_join(project_root, rel,
                                              allow_external=settings.external_paths)
        except file_manager.PathSafetyError as exc:
            ch.valid, ch.status = False, "invalid"
            ch.validation_errors = [str(exc)]
            result.errors.append(f"{ch.id}: {exc}")
            continue

        if ch.new_path:
            try:
                new_rel = file_manager.normalize_rel_path(ch.new_path)
                file_manager.safe_join(project_root, new_rel,
                                       allow_external=settings.external_paths)
                ch.new_path = new_rel
            except file_manager.PathSafetyError as exc:
                ch.valid, ch.status = False, "invalid"
                ch.validation_errors = [f"new_path: {exc}"]
                result.errors.append(f"{ch.id}: new_path {exc}")
                continue

        exists = file_manager.read_text(abs_path) is not None

        # --- binary guard ----------------------------------------------------
        # Text-level patch operations cannot be applied safely to BINARY
        # files; delete / rename still work (they do not touch content).
        if exists and _is_binary_file(abs_path) and ch.operation not in \
                ("delete", "rename"):
            ch.valid, ch.status = False, "invalid"
            msg = ("binary file - only delete/rename are supported for it")
            ch.validation_errors = [msg]
            result.binary_skipped.append(ch.id)
            result.errors.append(f"{ch.id}: {msg}")
            continue

        # --- operation semantics -------------------------------------------
        if ch.operation in ("delete", "rename", "replace", "insert_before",
                            "insert_after", "append", "prepend") and not exists \
                and ch.operation not in ("append", "prepend"):
            errs.append(f"target file does not exist: {rel}")
        if ch.operation == "create" and exists:
            errs.append(f"cannot create '{rel}': file already exists "
                        f"(use replace/append instead)")
        if ch.operation in ("replace", "insert_before", "insert_after") and \
                ch.target.is_empty() and ch.content is None:
            errs.append("no <target> given to locate the region")
        # regex replaces content with re.sub result; <content> holds the replacement template
        if ch.operation == "replace" and ch.target.regex and ch.content is None:
            errs.append("regex replace requires <content> (the replacement template, "
                        "may use \\1 backreferences)")
        if ch.operation in ("replace", "create", "append", "prepend",
                            "insert_before", "insert_after") and ch.content is None:
            errs.append("missing <content>/<replacement>")
        if ch.operation == "rename" and not ch.new_path:
            errs.append("rename requires <new_path>")

        # --- conflict detection (section 22) --------------------------------
        if settings.conflict_detection and snapshot_hashes is not None and exists:
            current_hash = file_manager.hash_file_or_text(abs_path)
            expected = snapshot_hashes.get(rel)
            if expected is not None and expected != current_hash:
                ch.status = "conflict"
                ch.validation_errors = [
                    "CONFLICT: the file changed after the prompt was generated. "
                    "Use Refresh / Regenerate, or Force Apply."]
                result.conflicts.append(ch.id)
                continue

        # --- locate target for region ops -----------------------------------
        if ch.operation in ("replace", "insert_before", "insert_after") and exists:
            text = file_manager.read_text(abs_path) or ""
            span, kind = locate(ch, text, settings)
            if span is None:
                if kind and kind.startswith("regex_error:"):
                    errs.append(f"invalid regex pattern: {kind[len('regex_error:'):]}")
                elif kind and kind.startswith("symbol_error:"):
                    errs.append(_symbol_error_text(ch, kind[len("symbol_error:"):]))
                else:
                    errs.append("target not found (no automatic nearest-match "
                                "substitution - see section 54)")
            else:
                ch.located_range = span
                ch.match_kind = kind

        # --- multiple changes on same file bookkeeping (section 56) ---------
        seen_paths.setdefault(rel, []).append(ch.id)

        if errs:
            ch.valid, ch.status = False, "invalid"
            ch.validation_errors = errs
            result.errors.extend(f"{ch.id}: {e}" for e in errs)
        else:
            ch.valid, ch.status = True, "valid"
            result.ok_changes.append(ch)

    # overlapping regions inside one file are rejected deterministically
    for rel, ids in seen_paths.items():
        region_ids = [i for i in ids for c in changes
                      if c.id == i and c.located_range]
    return result
