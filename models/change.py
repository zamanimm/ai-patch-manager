"""Internal data model for a single proposed change.

From this point on in the pipeline, nothing looks at XML anymore:
the parser turns the LLM response into `Change` objects, and the
patch engine only ever sees these objects.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional

VALID_OPERATIONS = {
    "create",
    "delete",
    "replace",
    "append",       # end of file - or, WITH a <target>, directly after that line / block
    "prepend",      # start of file - or, WITH a <target>, directly before that line / block
    "rename",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class SymbolTarget:
    """Structural target using Tree-sitter to locate language constructs."""
    type: str = ""                     # function, method, class, struct, interface, enum, etc.
    name: str = ""                     # symbol name
    class_name: Optional[str] = None   # for methods: parent class name
    parameters: List[str] = field(default_factory=list)  # optional parameter signature


@dataclass
class Target:
    """Addressing information used to locate the region a change applies to."""
    match: Optional[str] = None          # exact original code (priority 2)
    hash: Optional[str] = None           # sha256 of the matched block
    line_start: Optional[int] = None     # 1-based line range (priority 4)
    line_end: Optional[int] = None
    regex: Optional[str] = None          # Python re pattern; replace-all with backrefs (priority 3)
    symbol: Optional[SymbolTarget] = None  # Tree-sitter structural target (priority 1 - highest)

    def is_empty(self) -> bool:
        return (self.match is None and self.hash is None
                and self.line_start is None and self.regex is None
                and self.symbol is None)


@dataclass
class Change:
    id: str
    path: str                                  # relative to project root (forward slashes)
    operation: str
    target: Target = field(default_factory=Target)
    content: Optional[str] = None              # replacement / inserted / created content
    new_path: Optional[str] = None             # for rename
    summary: str = ""
    reason: str = ""
    confidence: Optional[float] = None         # informational only - never replaces user approval
    edited: bool = False                       # proposed code was edited in the app (e.g. search & replace in patches)

    # --- state filled in by the validator / diff engine -------------------
    valid: bool = False
    status: str = "pending"                    # pending|valid|invalid|conflict|rejected|applied|failed
    validation_errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)   # non-blocking hints shown in the preview
    located_range: Optional[tuple] = None      # (start_idx, end_idx) zero-based half-open
    match_kind: str = ""                       # exact|hash|context|line|fuzzy
    old_text: str = ""
    new_text: str = ""
    unified_diff: str = ""
    selected: bool = True

    @property
    def has_anchor(self) -> bool:
        """True when the target says WHERE (a line / text / pattern / symbol)."""
        t = self.target
        return bool(t.match or t.regex or t.symbol or t.line_start)

    @property
    def is_anchored_insert(self) -> bool:
        """append / prepend WITH a target: new lines after / before that anchor."""
        return self.operation in ("append", "prepend") and self.has_anchor

    @property
    def is_region_op(self) -> bool:
        """Operations that work on a located line region of an existing file."""
        return self.operation == "replace" or self.is_anchored_insert

    @property
    def is_file_level(self) -> bool:
        """Operations that do not need a located target region."""
        return not self.is_region_op

    @property
    def display_operation(self) -> str:
        """Operation name for lists: says where an anchored insert goes."""
        if self.is_anchored_insert:
            return "append (after target)" if self.operation == "append" else "prepend (before target)"
        return self.operation

    def compute_hash(self) -> str:
        return sha256_text(self.content or "")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Change {self.id} {self.operation} {self.path} [{self.status}]>"
