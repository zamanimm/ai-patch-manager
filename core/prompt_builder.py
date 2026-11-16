"""Prompt builder (sections 7-10, 42-48).

Provides the pieces of the standard, LLM-independent XML prompt:
  - the <system> instructions + response schema (symbol targeting optional)
  - rendering of the selected files (full / diff / selection / context modes)
The prompt itself is assembled in Controller.build_full_xml_prompt().
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as xml_escape
from typing import List, Optional, Tuple

from core import file_manager

# Schema documentation: Four target modes (in priority order)
# 0. <symbol>            Tree-Sitter structural targeting (available grammars only)
#                        <symbol><type>function</type><name>foo</name></symbol>
#
# 1. <match>             Exact text matching (safest for small unique blocks)
#                        <match>exact source code</match>
#
# 2. <regex>             Pattern matching with backreferences (simple structural patterns only)
#                        <regex>pattern</regex> with <replacement>\1 backrefs</replacement>
#
# 3. <line_start>/<line_end>  Line ranges (when line numbers are known)
#                             <line_start>10</line_start> <line_end>15</line_end>
#
# Prefer symbol > exact match > regex > line ranges
# Never use ambiguous patterns. 0 or 2+ matches = rejected.

_SCHEMA_HEAD = r"""<response version="1.0">
    <status>success</status>
    <summary>Brief description of changes</summary>
    <!-- Optional: only if user MUST know an important issue/action; omit otherwise. -->
    <!-- <note type="warning">Critical user-visible consequence or required action</note> -->
    <changes>
        <!-- Target mode A: exact match -->
        <change id="chg-001" path="relative/file/path.ext" operation="replace">
            <target>
                <match><![CDATA[exact original code to replace]]></match>
                <line_start>10</line_start>
                <line_end>15</line_end>
            </target>
            <replacement><![CDATA[new code to replace target]]></replacement>
            <reason>Explanation of why this change was made</reason>
        </change>
        <!-- Target mode B: regex — replace ALL occurrences; supports \\1 backreferences -->
        <!-- Example 1: Simple rename -->
        <change id="chg-002" path="relative/file/path.ext" operation="replace">
            <target>
                <regex><![CDATA[(\w+)_old_suffix]]></regex>
            </target>
            <replacement><![CDATA[\\1_new_suffix]]></replacement>
            <reason>Rename all occurrences using a regex pattern</reason>
        </change>
        <!-- Example 2: Semantic — rename a function (structural refactoring via regex) -->
        <change id="chg-003" path="api.py" operation="replace">
            <target>
                <regex><![CDATA[def old_handler\(([^)]*)\):]]></regex>
            </target>
            <replacement><![CDATA[def new_handler(\1):]]></replacement>
            <reason>Rename old_handler function to new_handler</reason>
        </change>
        <!-- prepend / append WITH a <target>: add new code BEFORE / AFTER a specific line
             (the anchor is the line's text - no line numbers needed) -->
        <change id="chg-004" path="app.py" operation="prepend">
            <target>
                <match><![CDATA[def my_func():]]></match>
            </target>
            <replacement><![CDATA[# helper added above my_func]]></replacement>
            <reason>Add code directly above the line "def my_func():"</reason>
        </change>
        <change id="chg-005" path="app.py" operation="append">
            <target>
                <match><![CDATA[import os]]></match>
            </target>
            <replacement><![CDATA[import sys]]></replacement>
            <reason>Add code directly below the line "import os"</reason>
        </change>
"""

_SYMBOL_SCHEMA_EXAMPLES = {
    "python": r"""        <!-- Example 3: Symbol (Tree-Sitter) — Python grammar -->
        <change id="chg-006" path="example.py" operation="replace">
            <target>
                <symbol>
                    <type>function</type>
                    <name>calculate_total</name>
                </symbol>
            </target>
            <replacement><![CDATA[def calculate_total(items):
    return sum(items)]]></replacement>
            <reason>Replace the complete calculate_total function.</reason>
        </change>
""",
    "javascript": r"""        <!-- Example 3: Symbol (Tree-Sitter) — JavaScript grammar -->
        <change id="chg-006" path="example.js" operation="replace">
            <target>
                <symbol>
                    <type>function</type>
                    <name>calculateTotal</name>
                </symbol>
            </target>
            <replacement><![CDATA[function calculateTotal(items) {
    return items.reduce((total, item) => total + item, 0);
}]]></replacement>
            <reason>Replace the complete calculateTotal function.</reason>
        </change>
""",
    "typescript": r"""        <!-- Example 3: Symbol (Tree-Sitter) — TypeScript grammar -->
        <change id="chg-006" path="example.ts" operation="replace">
            <target>
                <symbol>
                    <type>function</type>
                    <name>calculateTotal</name>
                </symbol>
            </target>
            <replacement><![CDATA[function calculateTotal(items: number[]): number {
    return items.reduce((total, item) => total + item, 0);
}]]></replacement>
            <reason>Replace the complete calculateTotal function.</reason>
        </change>
""",
    "tsx": r"""        <!-- Example 3: Symbol (Tree-Sitter) — TSX grammar -->
        <change id="chg-006" path="example.tsx" operation="replace">
            <target>
                <symbol>
                    <type>function</type>
                    <name>Widget</name>
                </symbol>
            </target>
            <replacement><![CDATA[function Widget() {
    return <div>Updated</div>;
}]]></replacement>
            <reason>Replace the complete Widget function.</reason>
        </change>
""",
    "java": r"""        <!-- Example 3: Symbol (Tree-Sitter) — Java grammar -->
        <change id="chg-006" path="Example.java" operation="replace">
            <target>
                <symbol>
                    <type>method</type>
                    <name>calculateTotal</name>
                    <class>Calculator</class>
                </symbol>
            </target>
            <replacement><![CDATA[    int calculateTotal(int[] values) {
        int total = 0;
        for (int value : values) total += value;
        return total;
    }]]></replacement>
            <reason>Replace the calculateTotal method in Calculator.</reason>
        </change>
""",
    "c": r"""        <!-- Example 3: Symbol (Tree-Sitter) — C grammar -->
        <change id="chg-006" path="example.c" operation="replace">
            <target>
                <symbol>
                    <type>function</type>
                    <name>calculate_total</name>
                </symbol>
            </target>
            <replacement><![CDATA[int calculate_total(int a, int b) {
    return a + b;
}]]></replacement>
            <reason>Replace the complete calculate_total function.</reason>
        </change>
""",
    "cpp": r"""        <!-- Example 3: Symbol (Tree-Sitter) — C++ grammar -->
        <change id="chg-006" path="example.cpp" operation="replace">
            <target>
                <symbol>
                    <type>function</type>
                    <name>calculateTotal</name>
                </symbol>
            </target>
            <replacement><![CDATA[int calculateTotal(int a, int b) {
    return a + b;
}]]></replacement>
            <reason>Replace the complete calculateTotal function.</reason>
        </change>
""",
}


def _symbol_schema_example(symbol_languages=None) -> str:
    available = tuple(symbol_languages or ())
    for language in available:
        if language in _SYMBOL_SCHEMA_EXAMPLES:
            return _SYMBOL_SCHEMA_EXAMPLES[language]
    return _SYMBOL_SCHEMA_EXAMPLES["python"]


_SCHEMA_TAIL = r"""    </changes>
</response>"""


def protocol_schema(include_symbol: bool = True, symbol_languages=None) -> str:
    """XML response schema; Symbol example matches an installed grammar."""
    example = _symbol_schema_example(symbol_languages) if include_symbol else ""
    return _SCHEMA_HEAD + example + _SCHEMA_TAIL


PROTOCOL_SCHEMA = protocol_schema(True)


def build_project_tree(root: str, relative_paths) -> str:
    """Render a compact XML directory tree containing paths only.

    The input paths are the project's currently scanned files, so include /
    exclude rules are respected. No file is opened and no file content is
    included. Unsafe or non-relative paths are ignored defensively.
    """
    import os
    from pathlib import PurePosixPath
    from xml.sax.saxutils import quoteattr

    project_name = os.path.basename(os.path.normpath(root)) or root or "project"
    tree = {"dirs": {}, "files": []}
    safe_paths = []
    for raw in relative_paths or []:
        value = str(raw).replace("\\", "/").strip()
        if (not value or value.startswith("/")
                or (len(value) >= 2 and value[1] == ":")):
            continue
        path = PurePosixPath(value)
        if not path.parts or any(part in ("", ".", "..") for part in path.parts):
            continue
        safe_paths.append(path.parts)

    safe_paths = sorted(set(safe_paths), key=lambda p: tuple(x.casefold() for x in p))
    for parts in safe_paths:
        node = tree
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": []})
        node["files"].append(parts[-1])

    lines = [f"<project_tree root={quoteattr(project_name)} file_count={quoteattr(str(len(safe_paths)))}>"]

    def render(node, depth):
        pad = "  " * depth
        for dirname in sorted(node["dirs"], key=str.casefold):
            lines.append(f"{pad}<directory name={quoteattr(dirname)}>")
            render(node["dirs"][dirname], depth + 1)
            lines.append(f"{pad}</directory>")
        for filename in sorted(node["files"], key=str.casefold):
            lines.append(f"{pad}<file name={quoteattr(filename)} />")

    render(tree, 1)
    lines.append("</project_tree>")
    return "\n".join(lines)

# Extra rule groups appended to the system instructions (§ minimal change,
# hallucination prevention, language awareness, strict XML output).
MINIMAL_CHANGE_RULES = [
    "Always generate the smallest possible patch.",
    "Do not replace whole files unless explicitly requested.",
    "Preserve formatting, comments, and unrelated code.",
]

HALLUCINATION_RULES = [
    ("Never invent files, functions, variables, or APIs not present in the "
     "provided context unless creation is requested."),
]

LANGUAGE_RULES = [
    ("Respect the target language syntax, imports, dependencies, formatting, "
     "and conventions."),
]

STRICT_XML_RULES = [
    "Do not repeat the supplied source files or restate the user request outside the XML changes.",
]

OUTPUT_CONTRACT_TEXT = (
    "For code or file-editing requests, return exactly one XML response document "
    "conforming to the supplied response schema. Do not include questions, "
    "conversational answers, explanations, acknowledgments, plans, Markdown "
    "fences, or any text outside the XML. If a safe edit is impossible from "
    "the supplied context, do not guess or ask follow-up questions; return "
    "status no_changes with a concise summary and an empty changes element. "
    "Include at most one optional response-level <note type=\"warning\"> "
    "or <note type=\"info\"> only if there is an essential risk, "
    "dependency, manual step, or consequence that the user MUST know. "
    "Otherwise omit <note> entirely; never use it for ordinary summaries, "
    "explanations, or routine advice. Use warning only for genuine caution; "
    "notes are model-supplied and unverified."
)

# Core instructions (what goes inside <system>) - single source of truth.
# Symbol (Tree-Sitter) targeting is OPTIONAL: pass include_symbol=False to get
# the same instructions without any mention of <symbol> mode.
def core_system_instructions(include_symbol: bool = True, symbol_languages=None) -> List[str]:
    if include_symbol:
        target_modes = (
            "Target modes (in order of preference): "
            "(0) <symbol> for structural targeting via Tree-Sitter (HIGHEST POWER — for functions, classes, methods); "
            "(1) <match> for small unique code blocks (safest); "
            "(2) <line_start>/<line_end> when you know exact line numbers; "
            "(3) <regex> for simple structural refactoring (function names, signatures, imports). "
            "Prefer symbol mode for semantic constructs; exact match for complex logic; regex only for simple patterns.")
    else:
        target_modes = (
            "Target modes (in order of preference): "
            "(1) <match> for small unique code blocks (safest); "
            "(2) <line_start>/<line_end> when you know exact line numbers; "
            "(3) <regex> for simple structural refactoring (function names, signatures, imports). "
            "Prefer exact match for complex logic; regex only for simple patterns.")
    items = [
        "You are a precise code modification assistant.",
        OUTPUT_CONTRACT_TEXT,
        ("Wrap all code snippets inside CDATA sections to avoid XML parsing "
         "errors. Keep source indentation as actual spaces/tabs (not &#x20;), "
         "and never append Markdown copy-format backslashes to code lines."),
        ("Supported operations: create, delete, replace, append, prepend, rename."),
        ("TARGET STRATEGY: You have already seen the source code. Your job is to "
         "generate a PRECISE pattern that uniquely identifies the region to modify "
         "in THIS specific file — not a universal grammar. The application will "
         "reject the change if 0 matches are found OR if 2+ matches exist (ambiguous)."),
        target_modes,
    ]
    if include_symbol:
        items.append(
            "SYMBOL (Tree-Sitter) TARGETING: Use <symbol> mode to precisely locate language constructs. "
            "Example: <symbol><type>function</type><name>calculateControl</name></symbol>. "
            "Symbol types: function, method, class, struct, interface, enum, etc. "
            "Tree-Sitter resolves exact boundaries (no regex ambiguity, handles nested braces/MATLAB end). "
            "The replacement overwrites the symbol's FULL lines, so keep the original indentation "
            "(e.g. a method inside a class stays indented). Add <class> for methods to disambiguate. "
            "Available grammar names in this installation: "
            + ", ".join(symbol_languages or ("python", "javascript", "typescript", "java", "c", "cpp")) + ".")
    items += [
        ("REGEX FOR STRUCTURAL REFACTORING: Use regex for renaming functions, "
         "changing signatures, swapping decorators, or updating imports — cases "
         "where you are matching code STRUCTURE, not logic. Examples: "
         "`def old_func\\(` → `def new_func(` | "
         "`from old_lib import` → `from new_lib import` | "
         "`@decorator_old` → `@decorator_new`. "
         "DO NOT use regex for function bodies, control flow, or nested logic "
         "— use <match> mode instead."),
        ("APPEND / PREPEND: WITHOUT a <target> they add the code at the very END (append) or "
         "START (prepend) of the file. WITH a <target> they add it next to a specific line: "
         "append = directly BELOW the anchor, prepend = directly ABOVE it. The anchor is a <match> "
         "holding that line (or block) copied EXACTLY from the source - no line numbers needed - "
         "and it is kept. The <replacement> is inserted as new whole lines. To add code both before "
         "AND after the same line, send one prepend and one append with the same <match>. The anchor "
         "must occur exactly ONCE in the file (otherwise it is rejected as ambiguous): add the "
         "neighbouring line to make it unique. Keep the original indentation in <replacement> "
         "(e.g. code inside a class stays indented)."),
        ("SAFETY: The application validates strictly. If your regex matches 0 times "
         "or 2+ times, the change is rejected. If the file changed after you saw it, "
         "the hash will not match and the change is rejected. Therefore: be precise, "
         "use patterns specific to the source you received, and avoid ambiguous patterns."),
    ]
    return items


CORE_SYSTEM_INSTRUCTIONS = core_system_instructions(True)

ADDITIONAL_RULES = [
    '<match> must contain the EXACT original text so the patch can be located.',
    'Paths are relative to the project root. Never use "..".',
    "Every change needs a unique id like chg-001, chg-002, ...",
    ("rename requires <new_path>. Optional: <hash>, <notes>, <confidence>."),
]

# Ordered rule groups rendered after the schema example: (title, rules)
RULE_GROUPS = [
    ("Minimal change rules", MINIMAL_CHANGE_RULES),
    ("Prevent hallucination", HALLUCINATION_RULES),
    ("Language awareness", LANGUAGE_RULES),
    ("Avoid prompt repetition", STRICT_XML_RULES),
]


def _rule_groups_text() -> str:
    out: List[str] = []
    for title, rules in RULE_GROUPS:
        out.append(title + ":")
        out.extend(f"- {r}" for r in rules)
    return "\n".join(out)


# The pure patch-system instructions (plain text, Instructions tab).
SYSTEM_INSTRUCTIONS = """\
%s
XML Schema Format:
%s
Additional rules:
%s
%s""" % ("\n".join(CORE_SYSTEM_INSTRUCTIONS), PROTOCOL_SCHEMA,
         "\n".join("- %s" % r for r in ADDITIONAL_RULES),
         _rule_groups_text())

# Full protocol instruction block (shown in the Instructions tab).
# Instructions are CDATA text; the response schema is real nested XML so it
# remains readable instead of being displayed as a wall of &lt; and &gt;.
def _cdata(text: str) -> str:
    """Wrap text in CDATA, safely splitting the one forbidden terminator."""
    return "<![CDATA[" + str(text).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def render_system_block(include_symbol: bool = True, indent: str = "    ", symbol_languages=None) -> str:
    """Render one clean, well-formed system block with a structured schema."""
    pad = indent
    inner = pad + "    "
    instructions = (
        core_system_instructions(include_symbol, symbol_languages)
        + list(ADDITIONAL_RULES)
        + [rule for _, rules in RULE_GROUPS for rule in rules]
    )
    lines = [f"{pad}<system>"]
    instruction_text = "\n\n".join(instructions)
    lines.append(f"{inner}<instructions>{_cdata(instruction_text)}</instructions>")
    lines.append(f"{inner}<response_schema>")
    lines.extend(f"{inner}    {line}" for line in
                 protocol_schema(include_symbol, symbol_languages).splitlines())
    lines.append(f"{inner}</response_schema>")
    lines.append(f"{pad}</system>")
    return "\n".join(lines)


def build_protocol_instruction(include_symbol: bool = True, symbol_languages=None) -> str:
    """Return the reusable protocol as valid XML, without escaped schema text."""
    return "\n".join(("<prompt>",
                         render_system_block(include_symbol, "    ", symbol_languages),
                         "</prompt>"))




@dataclass
class PromptFile:
    path: str
    mode: str = "full"                     # full | diff | selection | context
    content: Optional[str] = None          # provided content for selection mode
    base_content: Optional[str] = None     # reference version for diff mode
    ranges: List[Tuple[int, int]] = field(default_factory=list)  # 1-based inclusive line ranges (selection / context modes)
    context_lines: int = 3                 # how many surrounding lines to include in 'context' mode
    include_binary: bool = False           # base64 binary content only on explicit opt-in


def _apply_ranges(text: str, ranges: List[Tuple[int, int]]) -> str:
    """Return only the given 1-based inclusive line ranges of *text*.

    Ranges are sorted, merged when overlapping/adjacent, and clamped to the
    file length. Output keeps original line numbers annotated as comments so
    the LLM can map snippets back to the real file.
    """
    if not text:
        return ""
    lines = text.splitlines()
    total = len(lines)
    norm: List[Tuple[int, int]] = []
    for start, end in ranges:
        s = max(1, int(start))
        e = min(total, int(end))
        if s > e or s > total:
            continue
        norm.append((s, e))
    if not norm:
        return ""
    norm.sort()
    merged: List[Tuple[int, int]] = [norm[0]]
    for s, e in norm[1:]:
        ls, le = merged[-1]
        if s <= le + 1:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    out: List[str] = []
    for idx, (s, e) in enumerate(merged):
        if idx:
            out.append("...")
        out.append(f"# ---- lines {s}-{e} ----")
        out.extend(lines[s - 1:e])
    return "\n".join(out)


def _expand_context(ranges: List[Tuple[int, int]], pad: int, total: int) -> List[Tuple[int, int]]:
    """Grow each range by *pad* lines on both sides (for 'context' mode)."""
    if not ranges:
        return [(1, total)] if total else []
    return [(max(1, s - pad), min(total, e + pad)) for s, e in ranges]


class PromptFileUnavailableError(RuntimeError):
    """A selected project file could not be safely read for the prompt."""


def _render_files(files: List[PromptFile], project_root: str,
                  snapshot_hashes: Optional[dict] = None) -> str:
    out: List[str] = []
    for pf in files:
        try:
            abs_path = file_manager.safe_join(project_root, pf.path)
        except file_manager.PathSafetyError as exc:
            raise PromptFileUnavailableError(
                f"Selected file '{pf.path}' is outside the project root or has an unsafe path: {exc}"
            ) from exc

        # Read a fresh snapshot every time. Missing or inaccessible selected
        # files must raise an error, never become "not yet created" placeholders.
        try:
            snapshot = file_manager.read_prompt_snapshot(
                abs_path, include_binary=pf.include_binary)
        except FileNotFoundError as exc:
            raise PromptFileUnavailableError(
                f"Selected file '{pf.path}' is not present under project root "
                f"'{project_root}'. The file may have been moved or deleted; "
                "rescan the Project tab and select it again."
            ) from exc
        except PermissionError as exc:
            raise PromptFileUnavailableError(
                f"Selected file '{pf.path}' exists at '{abs_path}' but access was denied. "
                "Check its permissions or whether another application is locking it."
            ) from exc
        except (OSError, MemoryError, TypeError, ValueError) as exc:
            detail = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
            raise PromptFileUnavailableError(
                f"Could not read selected file '{pf.path}' at '{abs_path}': {detail}. "
                "No source content was added to the prompt."
            ) from exc

        current = snapshot.content
        if snapshot_hashes is not None:
            snapshot_hashes[pf.path] = snapshot.sha256
        if snapshot.is_binary:
            mode = "binary-base64" if pf.include_binary else "binary"
            out.append(f'<file path="{pf.path}" mode="{mode}" '
                       f'size="{snapshot.size}" sha256="{snapshot.sha256}">\n'
                       f'<![CDATA[\n{current}\n]]>\n</file>')
            continue
        if pf.mode == "diff":
            base = pf.base_content if pf.base_content is not None else ""
            diff = "".join(difflib.unified_diff(
                base.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile=f"a/{pf.path} (reference)", tofile=f"b/{pf.path} (current)"))
            body = diff if diff.strip() else "[no differences]"
            out.append(f'<file path="{pf.path}" mode="diff">\n'
                       f'<![CDATA[\n{body}\n]]>\n</file>')
        elif pf.mode == "selection":
            # Explicit line ranges chosen in the code viewer; fall back to any
            # manually-provided content; otherwise render whole file as a note.
            body = ""
            if pf.ranges:
                body = _apply_ranges(current, pf.ranges)
            if not body and pf.content:
                body = pf.content
            if not body:
                body = current  # no range picked yet -> include full file
                out.append(f'<file path="{pf.path}" mode="full" '
                           f'sha256="{snapshot.sha256}">\n'
                           f'<![CDATA[\n{body}\n]]>\n</file>')
                continue
            rngs = ", ".join(f"{s}-{e}" for s, e in sorted(pf.ranges)) or "manual"
            out.append(f'<file path="{pf.path}" mode="selection" lines="{rngs}">\n'
                       f'<![CDATA[\n{body}\n]]>\n</file>')
        elif pf.mode == "context":
            # Surrounding context around the picked ranges (or whole file when
            # nothing was picked). Padding comes from pf.context_lines.
            total = len(current.splitlines())
            ranges = pf.ranges or [(1, total)]
            expanded = _expand_context(ranges, pf.context_lines, total)
            body = _apply_ranges(current, expanded)
            pad = pf.context_lines
            out.append(f'<file path="{pf.path}" mode="context" padding="{pad}">\n'
                       f'<![CDATA[\n{body}\n]]>\n</file>')
        else:  # full (default)
            out.append(f'<file path="{pf.path}" mode="full" '
                       f'sha256="{snapshot.sha256}">\n'
                       f'<![CDATA[\n{current}\n]]>\n</file>')
    return "\n\n".join(out)
