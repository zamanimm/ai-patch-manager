"""Prompt builder (sections 7-10, 42-48).

Generates a standard, LLM-independent prompt from:
  - an XML template with {{PLACEHOLDER}} slots
  - selected files in full / diff mode
  - the user task and captured errors
  - user-defined rules
Adds a context preview summary (files / lines / estimated tokens).
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core import file_manager
from models.change import sha256_text

# Schema documentation: Four target modes (in priority order)
# 0. <symbol>            Tree-Sitter structural targeting (50+ languages, no ambiguity)
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
"""

_SCHEMA_SYMBOL_EXAMPLE = r"""        <!-- Example 3: Symbol (Tree-Sitter) — PREFERRED for semantic targeting -->
        <change id="chg-004" path="controller.m" operation="replace">
            <target>
                <symbol>
                    <type>function</type>
                    <name>calculateControl</name>
                </symbol>
            </target>
            <replacement><![CDATA[function y = newController(x)
    y = x * 2;
end]]></replacement>
            <reason>Replace entire calculateControl function with optimized version</reason>
        </change>
"""

_SCHEMA_TAIL = r"""    </changes>
</response>"""


def protocol_schema(include_symbol: bool = True) -> str:
    """XML response schema example; the <symbol> example is optional."""
    return _SCHEMA_HEAD + (_SCHEMA_SYMBOL_EXAMPLE if include_symbol else "") + _SCHEMA_TAIL


PROTOCOL_SCHEMA = protocol_schema(True)

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
    "No markdown, explanations, or text outside the XML response.",
    "Avoid nested CDATA issues in the schema example.",
]

# Core instructions (what goes inside <system>) - single source of truth.
# Symbol (Tree-Sitter) targeting is OPTIONAL: pass include_symbol=False to get
# the same instructions without any mention of <symbol> mode.
def core_system_instructions(include_symbol: bool = True) -> List[str]:
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
        ("You MUST return ONLY a structured XML response following the specified "
         "schema below."),
        ("Wrap all code snippets inside CDATA sections to avoid XML parsing "
         "errors."),
        ("Supported operations: create, delete, replace, insert_before, "
         "insert_after, append, prepend, rename."),
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
            "Supported: Python, JavaScript/TypeScript, Java, C/C++.")
    items += [
        ("REGEX FOR STRUCTURAL REFACTORING: Use regex for renaming functions, "
         "changing signatures, swapping decorators, or updating imports — cases "
         "where you are matching code STRUCTURE, not logic. Examples: "
         "`def old_func\\(` → `def new_func(` | "
         "`from old_lib import` → `from new_lib import` | "
         "`@decorator_old` → `@decorator_new`. "
         "DO NOT use regex for function bodies, control flow, or nested logic "
         "— use <match> mode instead."),
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
    "Output ONLY the XML document. No markdown fences, no commentary.",
]

# Ordered rule groups rendered after the schema example: (title, rules)
RULE_GROUPS = [
    ("Minimal change rules", MINIMAL_CHANGE_RULES),
    ("Prevent hallucination", HALLUCINATION_RULES),
    ("Language awareness", LANGUAGE_RULES),
    ("Ensure strict XML output", STRICT_XML_RULES),
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

# Full protocol instruction block embedded in generated prompts ({{PROTOCOL}}).
# include_symbol=False drops every mention of Tree-Sitter <symbol> targeting.
def build_protocol_instruction(include_symbol: bool = True) -> str:
    lines = ["<prompt>", "    <system>"]
    for instr in (
        [f"<instruction>{t}</instruction>" for t in core_system_instructions(include_symbol)]
        + ["<instruction>", "XML Schema Format:", *protocol_schema(include_symbol).splitlines(), "</instruction>"]
        + [f"<instruction>{t}</instruction>" for t in ADDITIONAL_RULES]
        + [f"<instruction>{t}</instruction>" for t in
           [r for _, rules in RULE_GROUPS for r in rules]]
    ):
        lines.extend("    " + l for l in instr.splitlines())
    lines.append("    </system>")
    lines.append("</prompt>")
    return "\n".join(lines)


PROTOCOL_INSTRUCTION = build_protocol_instruction(True)


@dataclass
class PromptFile:
    path: str
    mode: str = "full"                     # full | diff | selection | context
    content: Optional[str] = None          # provided content for selection mode
    base_content: Optional[str] = None     # reference version for diff mode
    ranges: List[Tuple[int, int]] = field(default_factory=list)  # 1-based inclusive line ranges (selection / context modes)
    context_lines: int = 3                 # how many surrounding lines to include in 'context' mode


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


@dataclass
class BuiltPrompt:
    text: str
    files_count: int = 0
    lines_count: int = 0
    est_tokens: int = 0
    errors_count: int = 0
    task: str = ""
    snapshot_hashes: Dict[str, str] = field(default_factory=dict)


def _render_files(files: List[PromptFile], project_root: str) -> str:
    out: List[str] = []
    for pf in files:
        abs_path = file_manager.safe_join(project_root, pf.path)
        current = file_manager.read_text_any(abs_path)
        if current is None:
            out.append(f'<file path="{pf.path}" mode="{pf.mode}">'
                       f'<![CDATA[[file does not exist yet]]]></file>')
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
                           f'sha256="{sha256_text(current)}">\n'
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
                       f'sha256="{sha256_text(current)}">\n'
                       f'<![CDATA[\n{current}\n]]>\n</file>')
    return "\n\n".join(out)


def build_prompt(template_text: str, project_name: str, project_root: str,
                 files: List[PromptFile], task: str,
                 errors: str = "", rules: Optional[List[str]] = None) -> BuiltPrompt:
    """Fill a {{PLACEHOLDER}} template. Falls back to a sane default layout."""
    files_block = _render_files(files, project_root)
    rules_block = "\n".join(f"- {r}" for r in (rules or []))

    text = template_text
    replacements = {
        "{{PROTOCOL}}": PROTOCOL_INSTRUCTION,
        "{{PROJECT_NAME}}": project_name,
        "{{PROJECT_ROOT}}": project_root,
        "{{TASK}}": task.strip(),
        "{{ERRORS}}": errors.strip() or "[none provided]",
        "{{FILES}}": files_block,
        "{{RULES}}": rules_block or "[none]",
    }
    for key, value in replacements.items():
        text = text.replace(key, value)

    total_lines = sum(text.count("\n") for _ in [0])  # single pass below
    lines = text.count("\n") + 1
    built = BuiltPrompt(
        text=text,
        files_count=len(files),
        lines_count=lines,
        est_tokens=int(len(text) / 4),               # crude ~4 chars/token estimate
        errors_count=errors.count("\n") + 1 if errors.strip() else 0,
        task=task,
        snapshot_hashes=_snapshot(files, project_root),
    )
    return built


def _snapshot(files: List[PromptFile], project_root: str) -> Dict[str, str]:
    """Hashes of the file state the LLM saw - basis for conflict detection (§22).

    Uses RAW-BYTE hashes so binary files are covered exactly as they are on
    disk (matches file_manager.hash_file_or_text used by the validator).
    """
    snaps: Dict[str, str] = {}
    for pf in files:
        abs_path = file_manager.safe_join(project_root, pf.path)
        h = file_manager.hash_file_or_text(abs_path)
        if h:
            snaps[pf.path] = h
    return snaps


def load_template(templates_dir: str, name: str) -> str:
    path = os.path.join(templates_dir, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"prompt template not found: {name}")
    return file_manager.read_text(path) or ""


def list_templates(templates_dir: str) -> List[str]:
    if not os.path.isdir(templates_dir):
        return []
    return sorted(f for f in os.listdir(templates_dir) if f.endswith(".xml"))
